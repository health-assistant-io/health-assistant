"""Family cloud-files engine (ADR-0052).

Canonical copy lives in the family standards (``contracts/cloud_files.py``) and is
vendored byte-identical into product hosts via
``the family sync tooling``. Edit the canonical copy only.

One normalized file shape (:class:`CloudFile`) and one transport seam for
WebDAV (Nextcloud, Fastmail, self-hosted), Google Drive, Microsoft Graph
OneDrive and Dropbox. As with the calendar module: auth is injected, no
credential lives in this module's state, and the transport client is
injectable (``httpx.AsyncClient`` in production, ``httpx.MockTransport`` in
tests).

The contract intentionally does **not** implement a sync engine: file
mirroring is a product decision (what to fetch, where to put it, how much
disk, what to do on conflict). It gives hosts the primitives — list, stat,
download, upload, mkdir, delete, move — with stable remote ids and etags.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

UTC = timezone.utc
DEFAULT_PAGE_SIZE = 200
MAX_UPLOAD_SIMPLE_BYTES = 4 * 1024 * 1024  # Graph simple-upload ceiling

_DAV_NS = {"d": "DAV:"}


class FileError(Exception):
    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        self.code = code
        self.detail = detail[:500]
        self.retryable = retryable
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class ConflictPolicy(StrEnum):
    FAIL = "fail"
    OVERWRITE = "overwrite"
    RENAME = "rename"


@dataclass(frozen=True)
class CloudFile:
    remote_id: str
    name: str
    path: str
    is_dir: bool = False
    size: int | None = None
    etag: str | None = None
    modified_at: datetime | None = None
    content_type: str | None = None
    provider: str | None = None

    @property
    def extension(self) -> str:
        suffix = posixpath.splitext(self.name)[1]
        return suffix.lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "remoteId": self.remote_id,
            "name": self.name,
            "path": self.path,
            "isDir": self.is_dir,
            "size": self.size,
            "etag": self.etag,
            "modifiedAt": self.modified_at.astimezone(UTC).isoformat() if self.modified_at else None,
            "contentType": self.content_type,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class FilePage:
    files: list[CloudFile]
    next_page_token: str | None = None


AuthProvider = Callable[[], "str | Awaitable[str]"]


class FileTransport(Protocol):
    writable: bool

    async def list(self, path: str = "", *, page_token: str | None = None) -> FilePage: ...

    async def stat(self, path: str) -> CloudFile: ...

    async def download(self, path: str) -> bytes: ...

    async def upload(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        etag: str | None = None,
        on_conflict: ConflictPolicy | str = ConflictPolicy.FAIL,
        remote_id: str | None = None,
    ) -> CloudFile: ...

    async def mkdir(self, path: str) -> CloudFile: ...

    async def delete(self, path: str) -> None: ...

    async def move(self, path: str, destination: str) -> CloudFile: ...

    async def aclose(self) -> None: ...


async def _resolve_auth(auth: AuthProvider | None) -> str | None:
    if auth is None:
        return None
    value = auth()
    if hasattr(value, "__await__"):
        value = await value  # type: ignore[assignment]
    return str(value) if value else None


def basic_auth(username: str, password: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")


def bearer_auth(token: str) -> str:
    return f"Bearer {token}"


def join_path(*parts: str) -> str:
    """POSIX-style join that keeps a leading slash and refuses traversal."""
    cleaned: list[str] = []
    for part in parts:
        for piece in str(part).replace("\\", "/").split("/"):
            if piece in ("", "."):
                continue
            if piece == "..":
                raise FileError("invalid", "path traversal is not allowed")
            cleaned.append(piece)
    return "/".join(cleaned)


def safe_name(name: str) -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]
    if not name or name in (".", ".."):
        raise FileError("invalid", "invalid file name")
    return name


def guess_content_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _HttpFiles:
    def __init__(self, client: Any, auth: AuthProvider | None = None, *, timeout: float = 30.0) -> None:
        self._client = client
        self._auth = auth
        self._timeout = timeout

    async def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        auth = await _resolve_auth(self._auth)
        if auth:
            headers["Authorization"] = auth
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                url,
                headers=await self._headers(headers),
                content=content,
                params=params,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise FileError("unreachable", str(exc), retryable=True) from exc
        if response.status_code in (401, 403):
            raise FileError("auth", f"{method} -> {response.status_code}")
        if response.status_code == 429:
            raise FileError("rate-limited", "provider asked to slow down", retryable=True)
        if response.status_code >= 500:
            raise FileError("provider", f"{method} -> {response.status_code}", retryable=True)
        return response

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


# ---------------------------------------------------------------------------
# WebDAV
# ---------------------------------------------------------------------------


def _dav_href_path(base: str, href: str, collection_root: str) -> str:
    path = urlparse(href).path or href
    marker = collection_root.rstrip("/") + "/"
    if marker in path:
        return path.split(marker, 1)[1].strip("/")
    return path.rstrip("/").rsplit("/", 1)[-1]


def _parse_dav_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw).astimezone(UTC)
    except (TypeError, ValueError):
        return None


class WebDavTransport(_HttpFiles):
    """WebDAV file collection (Nextcloud, Fastmail, self-hosted)."""

    def __init__(
        self,
        client: Any,
        base_url: str,
        *,
        auth: AuthProvider | None = None,
        timeout: float = 30.0,
        writable: bool = True,
        provider: str | None = "webdav",
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.base_url = base_url.rstrip("/") + "/"
        self.collection_root = urlparse(self.base_url).path.rstrip("/")
        self.writable = writable
        self.provider = provider

    def _url(self, path: str = "") -> str:
        if not path:
            return self.base_url
        return f"{self.base_url}{quote(join_path(path), safe='/')}"

    async def list(self, path: str = "", *, page_token: str | None = None) -> FilePage:
        body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/><d:resourcetype/><d:getcontentlength/>
    <d:getcontenttype/><d:getlastmodified/><d:getetag/>
  </d:prop>
</d:propfind>"""
        response = await self._request(
            "PROPFIND",
            self._url(path),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=body,
        )
        if response.status_code not in (207, 200):
            raise FileError("provider", f"PROPFIND -> {response.status_code}")
        root = ET.fromstring(response.text)
        files: list[CloudFile] = []
        base_path = join_path(path)
        for node in root.findall("d:response", _DAV_NS):
            href = node.findtext("d:href", default="", namespaces=_DAV_NS) or ""
            props = node.find("d:propstat/d:prop", _DAV_NS)
            if props is None:
                continue
            rel = _dav_href_path(self.base_url, href, self.collection_root)
            if not rel or rel == base_path:
                continue
            resource = props.find("d:resourcetype", _DAV_NS)
            is_dir = resource is not None and resource.find("d:collection", _DAV_NS) is not None
            name = props.findtext("d:displayname", default="", namespaces=_DAV_NS) or rel.rsplit("/", 1)[-1]
            size_raw = props.findtext("d:getcontentlength", default="", namespaces=_DAV_NS)
            files.append(
                CloudFile(
                    remote_id=self._url(rel),
                    name=name,
                    path=rel,
                    is_dir=is_dir,
                    size=int(size_raw) if size_raw.isdigit() else None,
                    etag=props.findtext("d:getetag", default="", namespaces=_DAV_NS) or None,
                    modified_at=_parse_dav_time(
                        props.findtext("d:getlastmodified", default="", namespaces=_DAV_NS)
                    ),
                    content_type=props.findtext("d:getcontenttype", default="", namespaces=_DAV_NS)
                    or None,
                    provider=self.provider,
                )
            )
        return FilePage(files=sorted(files, key=lambda entry: (not entry.is_dir, entry.name.lower())))

    async def stat(self, path: str) -> CloudFile:
        body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop>
  <d:displayname/><d:resourcetype/><d:getcontentlength/><d:getcontenttype/>
  <d:getlastmodified/><d:getetag/>
</d:prop></d:propfind>"""
        response = await self._request(
            "PROPFIND",
            self._url(path),
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            content=body,
        )
        if response.status_code == 404:
            raise FileError("not-found", f"no such path {path!r}")
        if response.status_code not in (207, 200):
            raise FileError("provider", f"PROPFIND -> {response.status_code}")
        root = ET.fromstring(response.text)
        node = root.find("d:response", _DAV_NS)
        props = node.find("d:propstat/d:prop", _DAV_NS) if node is not None else None
        if props is None:
            raise FileError("provider", "PROPFIND returned no properties")
        resource = props.find("d:resourcetype", _DAV_NS)
        is_dir = resource is not None and resource.find("d:collection", _DAV_NS) is not None
        size_raw = props.findtext("d:getcontentlength", default="", namespaces=_DAV_NS)
        return CloudFile(
            remote_id=self._url(path),
            name=props.findtext("d:displayname", default="", namespaces=_DAV_NS)
            or posixpath.basename(path),
            path=join_path(path),
            is_dir=is_dir,
            size=int(size_raw) if size_raw.isdigit() else None,
            etag=props.findtext("d:getetag", default="", namespaces=_DAV_NS) or None,
            modified_at=_parse_dav_time(
                props.findtext("d:getlastmodified", default="", namespaces=_DAV_NS)
            ),
            content_type=props.findtext("d:getcontenttype", default="", namespaces=_DAV_NS) or None,
            provider=self.provider,
        )

    async def download(self, path: str) -> bytes:
        response = await self._request("GET", self._url(path))
        if response.status_code == 404:
            raise FileError("not-found", f"no such file {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"GET -> {response.status_code}")
        return response.content

    async def upload(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        etag: str | None = None,
        on_conflict: ConflictPolicy | str = ConflictPolicy.FAIL,
        remote_id: str | None = None,
    ) -> CloudFile:
        if not self.writable:
            raise FileError("unsupported", "collection is read-only")
        target = join_path(path)
        if remote_id:
            target = target or posixpath.basename(urlparse(remote_id).path)
        headers = {"Content-Type": content_type or guess_content_type(target)}
        if etag:
            headers["If-Match"] = etag
        elif ConflictPolicy(on_conflict) == ConflictPolicy.FAIL:
            headers["If-None-Match"] = "*"
        response = await self._request(
            "PUT", self._url(target), headers=headers, content=data
        )
        if response.status_code == 412:
            if ConflictPolicy(on_conflict) == ConflictPolicy.RENAME:
                stem, suffix = posixpath.splitext(target)
                target = f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
                return await self.upload(target, data, content_type=content_type)
            raise FileError("invalid", "remote file changed since it was read")
        if response.status_code >= 300:
            raise FileError("provider", f"PUT -> {response.status_code}")
        return await self.stat(target)

    async def mkdir(self, path: str) -> CloudFile:
        if not self.writable:
            raise FileError("unsupported", "collection is read-only")
        response = await self._request("MKCOL", self._url(path))
        if response.status_code not in (201, 405, 301):
            raise FileError("provider", f"MKCOL -> {response.status_code}")
        return await self.stat(path)

    async def delete(self, path: str) -> None:
        if not self.writable:
            raise FileError("unsupported", "collection is read-only")
        response = await self._request("DELETE", self._url(path))
        if response.status_code not in (200, 204, 404, 410):
            raise FileError("provider", f"DELETE -> {response.status_code}")

    async def move(self, path: str, destination: str) -> CloudFile:
        if not self.writable:
            raise FileError("unsupported", "collection is read-only")
        response = await self._request(
            "MOVE",
            self._url(path),
            headers={"Destination": self._url(destination), "Overwrite": "F"},
        )
        if response.status_code == 412:
            raise FileError("invalid", "destination exists")
        if response.status_code >= 300:
            raise FileError("provider", f"MOVE -> {response.status_code}")
        return await self.stat(destination)


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


class GoogleDriveTransport(_HttpFiles):
    writable = True

    def __init__(
        self,
        client: Any,
        *,
        api_base: str = "https://www.googleapis.com/drive/v3",
        upload_base: str = "https://www.googleapis.com/upload/drive/v3",
        folder_id: str = "root",
        auth: AuthProvider | None = None,
        timeout: float = 30.0,
        page_size: int = DEFAULT_PAGE_SIZE,
        provider: str | None = "google-drive",
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.api_base = api_base.rstrip("/")
        self.upload_base = upload_base.rstrip("/")
        self.folder_id = folder_id
        self.page_size = page_size
        self.provider = provider

    def _fields(self) -> str:
        return "nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum,parents)"

    def _to_file(self, item: Mapping[str, Any], path: str = "") -> CloudFile:
        is_dir = item.get("mimeType") == "application/vnd.google-apps.folder"
        return CloudFile(
            remote_id=str(item.get("id")),
            name=str(item.get("name") or ""),
            path=join_path(path, str(item.get("name") or "")) if path else str(item.get("name") or ""),
            is_dir=is_dir,
            size=int(item["size"]) if str(item.get("size") or "").isdigit() else None,
            etag=item.get("md5Checksum"),
            modified_at=_parse_rfc3339(item.get("modifiedTime")),
            content_type=item.get("mimeType"),
            provider=self.provider,
        )

    async def _resolve_folder(self, path: str) -> str:
        current = self.folder_id
        for segment in join_path(path).split("/") if path else []:
            if not segment:
                continue
            query = (
                f"'{current}' in parents and name = '{segment.replace(chr(39), chr(92) + chr(39))}' "
                "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            )
            response = await self._request(
                "GET",
                f"{self.api_base}/files",
                params={"q": query, "fields": "files(id,name)", "pageSize": 1},
            )
            if response.status_code >= 300:
                raise FileError("provider", f"resolve folder -> {response.status_code}")
            items = response.json().get("files") or []
            if not items:
                raise FileError("not-found", f"folder {path!r} not found")
            current = str(items[0]["id"])
        return current

    async def list(self, path: str = "", *, page_token: str | None = None) -> FilePage:
        folder = await self._resolve_folder(path)
        params: dict[str, Any] = {
            "q": f"'{folder}' in parents and trashed = false",
            "fields": self._fields(),
            "pageSize": self.page_size,
            "orderBy": "folder,name",
        }
        if page_token:
            params["pageToken"] = page_token
        response = await self._request("GET", f"{self.api_base}/files", params=params)
        if response.status_code >= 300:
            raise FileError("provider", f"list -> {response.status_code}")
        payload = response.json()
        return FilePage(
            files=[self._to_file(item, path) for item in payload.get("files", [])],
            next_page_token=payload.get("nextPageToken"),
        )

    async def stat(self, path: str) -> CloudFile:
        parent, name = posixpath.split(join_path(path))
        folder = await self._resolve_folder(parent)
        query = f"'{folder}' in parents and name = '{name.replace(chr(39), chr(92) + chr(39))}' and trashed = false"
        response = await self._request(
            "GET", f"{self.api_base}/files", params={"q": query, "fields": self._fields(), "pageSize": 1}
        )
        if response.status_code >= 300:
            raise FileError("provider", f"stat -> {response.status_code}")
        items = response.json().get("files") or []
        if not items:
            raise FileError("not-found", f"no such path {path!r}")
        return self._to_file(items[0], parent)

    async def download(self, path: str) -> bytes:
        entry = await self.stat(path)
        response = await self._request(
            "GET", f"{self.api_base}/files/{quote(entry.remote_id, safe='')}", params={"alt": "media"}
        )
        if response.status_code >= 300:
            raise FileError("provider", f"download -> {response.status_code}")
        return response.content

    async def upload(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        etag: str | None = None,
        on_conflict: ConflictPolicy | str = ConflictPolicy.FAIL,
        remote_id: str | None = None,
    ) -> CloudFile:
        parent_path, name = posixpath.split(join_path(path))
        parent = await self._resolve_folder(parent_path)
        existing_id = remote_id
        if existing_id is None:
            try:
                existing_id = (await self.stat(path)).remote_id
            except FileError as exc:
                if exc.code != "not-found":
                    raise
        if existing_id:
            if ConflictPolicy(on_conflict) == ConflictPolicy.RENAME:
                stem, suffix = posixpath.splitext(name)
                name = f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
                existing_id = None
            else:
                response = await self._request(
                    "PATCH",
                    f"{self.upload_base}/files/{quote(existing_id, safe='')}",
                    params={"uploadType": "media"},
                    headers={"Content-Type": content_type or guess_content_type(name)},
                    content=data,
                )
                if response.status_code >= 300:
                    raise FileError("provider", f"update -> {response.status_code}")
                return await self.stat(posixpath.join(parent_path, name))
        metadata = {"name": name, "parents": [parent]}
        boundary = f"nx-{uuid.uuid4().hex}"
        body = _multipart(
            boundary,
            metadata,
            data,
            content_type or guess_content_type(name),
        )
        response = await self._request(
            "POST",
            f"{self.upload_base}/files",
            params={"uploadType": "multipart", "fields": "id,name,mimeType,size,modifiedTime,md5Checksum"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        )
        if response.status_code >= 300:
            raise FileError("provider", f"upload -> {response.status_code}")
        return self._to_file(response.json(), parent_path)

    async def mkdir(self, path: str) -> CloudFile:
        parent_path, name = posixpath.split(join_path(path))
        parent = await self._resolve_folder(parent_path)
        response = await self._request(
            "POST",
            f"{self.api_base}/files",
            headers={"Content-Type": "application/json"},
            content=json.dumps(
                {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]}
            ),
        )
        if response.status_code >= 300:
            raise FileError("provider", f"mkdir -> {response.status_code}")
        return self._to_file(response.json(), parent_path)

    async def delete(self, path: str) -> None:
        entry = await self.stat(path)
        response = await self._request("DELETE", f"{self.api_base}/files/{quote(entry.remote_id, safe='')}")
        if response.status_code not in (200, 204, 404):
            raise FileError("provider", f"delete -> {response.status_code}")

    async def move(self, path: str, destination: str) -> CloudFile:
        entry = await self.stat(path)
        dest_parent, dest_name = posixpath.split(join_path(destination))
        parent = await self._resolve_folder(dest_parent)
        current = await self._request(
            "GET", f"{self.api_base}/files/{quote(entry.remote_id, safe='')}", params={"fields": "parents"}
        )
        if current.status_code >= 300:
            raise FileError("provider", f"move lookup -> {current.status_code}")
        remove = ",".join(str(value) for value in current.json().get("parents") or [])
        response = await self._request(
            "PATCH",
            f"{self.api_base}/files/{quote(entry.remote_id, safe='')}",
            params={"addParents": parent, "removeParents": remove},
            headers={"Content-Type": "application/json"},
            content=json.dumps({"name": dest_name}),
        )
        if response.status_code >= 300:
            raise FileError("provider", f"move -> {response.status_code}")
        return await self.stat(destination)


def _multipart(boundary: str, metadata: Mapping[str, Any], data: bytes, content_type: str) -> bytes:
    lines = [
        f"--{boundary}\r\n",
        "Content-Type: application/json; charset=UTF-8\r\n\r\n",
        json.dumps(metadata),
        f"\r\n--{boundary}\r\n",
        f"Content-Type: {content_type}\r\n\r\n",
    ]
    head = "".join(lines).encode("utf-8")
    return head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")


def _parse_rfc3339(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Microsoft Graph OneDrive
# ---------------------------------------------------------------------------


class GraphDriveTransport(_HttpFiles):
    writable = True

    def __init__(
        self,
        client: Any,
        *,
        api_base: str = "https://graph.microsoft.com/v1.0",
        drive: str = "me",
        auth: AuthProvider | None = None,
        timeout: float = 30.0,
        page_size: int = DEFAULT_PAGE_SIZE,
        provider: str | None = "graph-drive",
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.api_base = api_base.rstrip("/")
        self.drive = drive
        self.page_size = page_size
        self.provider = provider

    def _root(self) -> str:
        return f"{self.api_base}/{self.drive}/drive"

    def _item_url(self, path: str = "") -> str:
        clean = join_path(path)
        if not clean:
            return f"{self._root()}/root"
        return f"{self._root()}/root:/{quote(clean, safe='/')}"

    def _to_file(self, item: Mapping[str, Any], parent_path: str = "") -> CloudFile:
        is_dir = "folder" in item
        name = str(item.get("name") or "")
        return CloudFile(
            remote_id=str(item.get("id")),
            name=name,
            path=join_path(parent_path, name) if parent_path else name,
            is_dir=is_dir,
            size=int(item["size"]) if str(item.get("size") or "").isdigit() else None,
            etag=item.get("eTag"),
            modified_at=_parse_rfc3339(item.get("lastModifiedDateTime")),
            content_type=(item.get("file") or {}).get("mimeType"),
            provider=self.provider,
        )

    async def list(self, path: str = "", *, page_token: str | None = None) -> FilePage:
        url = page_token or f"{self._item_url(path)}/children"
        params = None
        if not page_token:
            params = {
                "$select": "id,name,size,file,folder,eTag,lastModifiedDateTime",
                "$top": self.page_size,
            }
        response = await self._request("GET", url, params=params)
        if response.status_code == 404:
            raise FileError("not-found", f"no such folder {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"list -> {response.status_code}")
        payload = response.json()
        return FilePage(
            files=[self._to_file(item, path) for item in payload.get("value", [])],
            next_page_token=payload.get("@odata.nextLink"),
        )

    async def stat(self, path: str) -> CloudFile:
        response = await self._request(
            "GET",
            self._item_url(path),
            params={"$select": "id,name,size,file,folder,eTag,lastModifiedDateTime"},
        )
        if response.status_code == 404:
            raise FileError("not-found", f"no such path {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"stat -> {response.status_code}")
        parent = posixpath.dirname(join_path(path))
        return self._to_file(response.json(), parent)

    async def download(self, path: str) -> bytes:
        response = await self._request("GET", f"{self._item_url(path)}:/content")
        if response.status_code == 404:
            raise FileError("not-found", f"no such file {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"download -> {response.status_code}")
        return response.content

    async def upload(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        etag: str | None = None,
        on_conflict: ConflictPolicy | str = ConflictPolicy.FAIL,
        remote_id: str | None = None,
    ) -> CloudFile:
        if len(data) > MAX_UPLOAD_SIMPLE_BYTES:
            raise FileError("unsupported", "files above 4 MiB need a Graph upload session")
        target = join_path(path)
        if ConflictPolicy(on_conflict) == ConflictPolicy.RENAME:
            try:
                await self.stat(target)
            except FileError as exc:
                if exc.code != "not-found":
                    raise
            else:
                stem, suffix = posixpath.splitext(target)
                target = f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
        headers = {"Content-Type": content_type or guess_content_type(target)}
        if etag and ConflictPolicy(on_conflict) == ConflictPolicy.FAIL:
            headers["If-Match"] = etag
        response = await self._request(
            "PUT",
            f"{self._item_url(target)}:/content",
            headers=headers,
            content=data,
        )
        if response.status_code == 412:
            raise FileError("invalid", "remote file changed since it was read")
        if response.status_code >= 300:
            raise FileError("provider", f"upload -> {response.status_code}")
        parent = posixpath.dirname(target)
        return self._to_file(response.json(), parent)

    async def mkdir(self, path: str) -> CloudFile:
        target = join_path(path)
        parent, name = posixpath.split(target)
        children_url = f"{self._item_url(parent)}:/children" if parent else f"{self._root()}/root/children"
        response = await self._request(
            "POST",
            children_url,
            headers={"Content-Type": "application/json"},
            content=json.dumps({"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}),
        )
        if response.status_code == 409:
            raise FileError("invalid", "folder already exists")
        if response.status_code >= 300:
            raise FileError("provider", f"mkdir -> {response.status_code}")
        return self._to_file(response.json(), parent)

    async def delete(self, path: str) -> None:
        entry = await self.stat(path)
        response = await self._request(
            "DELETE", f"{self._root()}/items/{quote(entry.remote_id, safe='')}"
        )
        if response.status_code not in (200, 204, 404):
            raise FileError("provider", f"delete -> {response.status_code}")

    async def move(self, path: str, destination: str) -> CloudFile:
        entry = await self.stat(path)
        dest_parent, dest_name = posixpath.split(join_path(destination))
        parent = await self.stat(dest_parent)
        response = await self._request(
            "PATCH",
            f"{self._root()}/items/{quote(entry.remote_id, safe='')}",
            headers={"Content-Type": "application/json"},
            content=json.dumps({"name": dest_name, "parentReference": {"id": parent.remote_id}}),
        )
        if response.status_code >= 300:
            raise FileError("provider", f"move -> {response.status_code}")
        return self._to_file(response.json(), dest_parent)


# ---------------------------------------------------------------------------
# Dropbox
# ---------------------------------------------------------------------------


class DropboxTransport(_HttpFiles):
    writable = True

    def __init__(
        self,
        client: Any,
        *,
        api_base: str = "https://api.dropboxapi.com/2",
        content_base: str = "https://content.dropboxapi.com/2",
        auth: AuthProvider | None = None,
        timeout: float = 30.0,
        provider: str | None = "dropbox",
    ) -> None:
        super().__init__(client, auth, timeout=timeout)
        self.api_base = api_base.rstrip("/")
        self.content_base = content_base.rstrip("/")
        self.provider = provider

    def _dbx_path(self, path: str) -> str:
        clean = join_path(path)
        return "/" + clean if clean else ""

    def _to_file(self, entry: Mapping[str, Any]) -> CloudFile:
        tag = entry.get(".tag")
        return CloudFile(
            remote_id=str(entry.get("id") or entry.get("path_lower") or entry.get("path_display")),
            name=str(entry.get("name") or posixpath.basename(entry.get("path_display") or "")),
            path=str(entry.get("path_display") or "").lstrip("/"),
            is_dir=tag == "folder",
            size=int(entry["size"]) if str(entry.get("size") or "").isdigit() else None,
            etag=entry.get("content_hash"),
            modified_at=_parse_rfc3339(entry.get("server_modified")),
            content_type=None,
            provider=self.provider,
        )

    async def list(self, path: str = "", *, page_token: str | None = None) -> FilePage:
        if page_token:
            response = await self._request(
                "POST",
                f"{self.api_base}/files/list_folder/continue",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"cursor": page_token}),
            )
        else:
            response = await self._request(
                "POST",
                f"{self.api_base}/files/list_folder",
                headers={"Content-Type": "application/json"},
                content=json.dumps(
                    {"path": self._dbx_path(path), "recursive": False, "include_deleted": False}
                ),
            )
        if response.status_code == 409:
            raise FileError("not-found", f"no such folder {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"list -> {response.status_code}")
        payload = response.json()
        return FilePage(
            files=[self._to_file(entry) for entry in payload.get("entries", [])],
            next_page_token=payload.get("cursor") if payload.get("has_more") else None,
        )

    async def stat(self, path: str) -> CloudFile:
        response = await self._request(
            "POST",
            f"{self.api_base}/files/get_metadata",
            headers={"Content-Type": "application/json"},
            content=json.dumps({"path": self._dbx_path(path)}),
        )
        if response.status_code == 409:
            raise FileError("not-found", f"no such path {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"stat -> {response.status_code}")
        return self._to_file(response.json())

    async def download(self, path: str) -> bytes:
        arg = json.dumps({"path": self._dbx_path(path)})
        response = await self._request(
            "POST",
            f"{self.content_base}/files/download",
            headers={"Dropbox-API-Arg": arg},
        )
        if response.status_code == 409:
            raise FileError("not-found", f"no such file {path!r}")
        if response.status_code >= 300:
            raise FileError("provider", f"download -> {response.status_code}")
        return response.content

    async def upload(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str | None = None,
        etag: str | None = None,
        on_conflict: ConflictPolicy | str = ConflictPolicy.FAIL,
        remote_id: str | None = None,
    ) -> CloudFile:
        if ConflictPolicy(on_conflict) == ConflictPolicy.OVERWRITE:
            mode = "overwrite"
        elif ConflictPolicy(on_conflict) == ConflictPolicy.RENAME:
            mode = "autorename"
        else:
            mode = "add"
        arg = json.dumps({"path": self._dbx_path(path), "mode": mode, "autorename": False, "mute": True})
        response = await self._request(
            "POST",
            f"{self.content_base}/files/upload",
            headers={"Dropbox-API-Arg": arg, "Content-Type": "application/octet-stream"},
            content=data,
        )
        if response.status_code == 409:
            raise FileError("invalid", "remote file already exists")
        if response.status_code >= 300:
            raise FileError("provider", f"upload -> {response.status_code}")
        return self._to_file(response.json())

    async def mkdir(self, path: str) -> CloudFile:
        response = await self._request(
            "POST",
            f"{self.api_base}/files/create_folder_v2",  # gate-allow: *, Dropbox API path
            headers={"Content-Type": "application/json"},
            content=json.dumps({"path": self._dbx_path(path), "autorename": False}),
        )
        if response.status_code == 409:
            raise FileError("invalid", "folder already exists")
        if response.status_code >= 300:
            raise FileError("provider", f"mkdir -> {response.status_code}")
        return self._to_file((response.json().get("metadata") or {}))

    async def delete(self, path: str) -> None:
        response = await self._request(
            "POST",
            f"{self.api_base}/files/delete_v2",  # gate-allow: *, Dropbox API path
            headers={"Content-Type": "application/json"},
            content=json.dumps({"path": self._dbx_path(path)}),
        )
        if response.status_code not in (200, 409):
            raise FileError("provider", f"delete -> {response.status_code}")

    async def move(self, path: str, destination: str) -> CloudFile:
        response = await self._request(
            "POST",
            f"{self.api_base}/files/move_v2",  # gate-allow: *, Dropbox API path
            headers={"Content-Type": "application/json"},
            content=json.dumps(
                {
                    "from_path": self._dbx_path(path),
                    "to_path": self._dbx_path(destination),
                    "autorename": False,
                }
            ),
        )
        if response.status_code == 409:
            raise FileError("invalid", "destination exists")
        if response.status_code >= 300:
            raise FileError("provider", f"move -> {response.status_code}")
        return self._to_file((response.json().get("metadata") or {}))


def transport_for(
    protocol: str,
    client: Any,
    *,
    base_url: str | None = None,
    auth: AuthProvider | None = None,
    folder_id: str = "root",
    provider: str | None = None,
    api_base: str | None = None,
    upload_base: str | None = None,
    content_base: str | None = None,
) -> FileTransport:
    """Build a file transport from a catalog ``protocol`` value."""
    if protocol == "webdav":
        if not base_url:
            raise FileError("invalid", "webdav needs a base URL")
        return WebDavTransport(client, base_url, auth=auth, provider=provider)
    if protocol == "google-drive":
        return GoogleDriveTransport(
            client,
            api_base=api_base or "https://www.googleapis.com/drive/v3",
            upload_base=upload_base or "https://www.googleapis.com/upload/drive/v3",
            folder_id=folder_id,
            auth=auth,
            provider=provider,
        )
    if protocol == "graph-drive":
        return GraphDriveTransport(
            client,
            api_base=api_base or "https://graph.microsoft.com/v1.0",
            auth=auth,
            provider=provider,
        )
    if protocol == "dropbox":
        return DropboxTransport(
            client,
            api_base=api_base or "https://api.dropboxapi.com/2",
            content_base=content_base or "https://content.dropboxapi.com/2",
            auth=auth,
            provider=provider,
        )
    raise FileError("unsupported", f"unknown files protocol {protocol!r}")


__all__ = [
    "CloudFile",
    "ConflictPolicy",
    "DEFAULT_PAGE_SIZE",
    "DropboxTransport",
    "FileError",
    "FilePage",
    "FileTransport",
    "GoogleDriveTransport",
    "GraphDriveTransport",
    "MAX_UPLOAD_SIMPLE_BYTES",
    "WebDavTransport",
    "basic_auth",
    "bearer_auth",
    "content_hash",
    "guess_content_type",
    "join_path",
    "safe_name",
    "transport_for",
]

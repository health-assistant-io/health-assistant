"""The seed source transport — the repo's first git/URL fetcher (ADR 0056
Phase 3, decision D11: one shared fetch transport built on ``polite_http``
and secret slots).

Data-only: the transport moves **pack bytes** (a manifest + hashed item
files) and never executes content — no code from a pack is imported, no
hook is invoked, no entry point exists. Three origins are supported:

* ``git`` — a pinned ref (branch/tag name or 40/64-hex commit) cloned
  **shallow** over HTTPS with the ``git`` CLI (no shell; argv list only),
  the pack read from ``subpath`` via the pack loader's path-safety rules.
  Auth rides the environment (``GIT_CONFIG_*`` → ``http.extraHeader``), never
  the URL or argv, so no credential is visible in a process list; HTTP
  redirects are disabled (``http.followRedirects=false``) so the checked
  host is the connected host.
* ``registry`` — an HTTPS GET of a pack archive (the upload zip shape) or a
  raw manifest+items response; every request runs the vendored SSRF guard
  (``polite_http`` resolve-then-connect, numeric-literal coercion, reserved
  ranges, IPv4-mapped IPv6), the guard-checked address is **pinned** into
  the request (original Host header + TLS SNI — no re-resolve at connect),
  every redirect hop re-runs the https-only grammar (a scheme downgrade is
  a typed refusal before the hop is requested) and is re-guarded and
  re-pinned, the ``Authorization`` header rides same-origin hops only
  (scheme+host+port — httpx's redirect rule), and the body is streamed
  under a byte cap.
* ``file`` — no fetch: content arrives through the admin upload door; sync
  on a file source is a no-op.

Threat model (``guidelines/security.md`` — the seed-pack lane):

* **SSRF** — ``file://``/``ftp://``/``ssh://`` and userinfo URLs are
  structurally refused (https-only grammar); loopback/link-local/RFC1918/
  RFC4193/reserved/multicast targets are refused by the vendored guard
  (``HA_SEED_SOURCE_ALLOWED_HOSTS`` exempts trusted self-hosted names;
  ``HA_SEED_SOURCE_ALLOW_PRIVATE`` is the wholesale hatch, default off);
  resolution failure during a fetch fails CLOSED; a redirect to a private
  target fails loudly instead of being followed.
* **Pins** — a failed fetch keeps every pin and lands in
  ``seed_sources.last_error``; the transport never mutates source state.
* **Limits** — per-fetch wall clock (``HA_SEED_FETCH_TIMEOUT_SECONDS``),
  download/stream byte caps (``HA_SEED_FETCH_MAX_BYTES``), archive
  expansion caps and an entry allowlist (``manifest.json`` + ``items/**.json``
  only — path traversal, backslash and absolute forms are typed refusals,
  the ``unpack_seeds_zip`` rules).
* **Secrets** — ``auth_secret_ref`` resolves via the OS keyring/env slot
  (:mod:`app.services.seed.secrets`); the value is injected per-request and
  redacted from every rendered failure.

No grammar lives here: the https-only URL, the bound git ref / commit sha,
the pack subpath and the archive entry allowlist come from the vendored
family parser (:mod:`app.extensions.extension_sources` — plan 60 D4, the
canonical ``extension-sources`` grammar). This module maps its typed codes
onto :class:`SeedTransportError` 1:1 and keeps the transport wording; the
SSRF policy stays ``polite_http``'s and the archive extraction stays here.

The module reads its knobs from Settings and nothing else is global; tests
inject an ``httpx.AsyncClient``-shaped transport and a resolver.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from nx_auth.secrets import redact_secret

from app.extensions import extension_sources, polite_http

logger = logging.getLogger(__name__)

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_CHUNK = 65536


class SeedTransportError(RuntimeError):
    """A typed transport refusal (nothing was fetched or written).

    ``code`` is one of: ``NET_DENIED`` (SSRF guard / grammar refusal),
    ``UNREACHABLE`` (resolution or transport failure — fail-closed),
    ``AUTH`` (upstream 401/403), ``PROVIDER`` (upstream 4xx/5xx, redirect
    cap), ``FETCH_TOO_LARGE`` (byte cap), ``FETCH_TIMEOUT`` (wall clock),
    ``REF_INVALID`` / ``URI_INVALID`` / ``SUBPATH_INVALID`` (input grammar),
    ``ARCHIVE_INVALID`` / ``ARCHIVE_ENTRY_FORBIDDEN`` / ``ARCHIVE_PATH_TRAVERSAL``
    / ``ARCHIVE_TOO_LARGE`` (upload/registry archive rules),
    ``PACK_NOT_FOUND`` (no manifest at the resolved location),
    ``UNSUPPORTED_KIND`` (the kind does not fetch — bundled/file).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class FetchedPack:
    """The pack one fetch produced: the parsed manifest plus its item files.

    ``manifest`` is the decoded ``manifest.json`` object; ``files`` maps the
    item-file paths (``items/**.json`` — the manifest itself is carried
    separately, matching the pack contract's ``parse_pack`` input shape).
    Pins ride alongside (the resolved commit sha / the response etag) and
    land on the new pending version row.
    """

    manifest: dict
    files: dict[str, bytes]
    commit_sha: str | None = None
    etag: str | None = None
    final_url: str | None = None
    not_modified: bool = False

    @property
    def is_not_modified(self) -> bool:
        """A conditional registry GET answered 304 — nothing new."""
        return self.not_modified


@dataclass(frozen=True)
class _FetchedBody:
    """The raw bytes of a registry response (pre-archive-unpack).

    ``not_modified`` distinguishes a real ``304`` answer from an empty
    ``200`` body — the two mean opposite things (nothing new vs. no pack).
    """

    body: bytes
    etag: str | None
    final_url: str
    not_modified: bool = False


def seed_net_guard(
    *, extra_allowed_hosts: frozenset[str] | set[str] = frozenset()
) -> polite_http.NetGuardConfig:
    """The guard policy from Settings (injectable — tests pass a config)."""
    from app.core.config import get_settings

    settings = get_settings()
    allowed = {
        h.strip().lower()
        for h in (settings.HA_SEED_SOURCE_ALLOWED_HOSTS or "").split(",")
        if h.strip()
    }
    allowed |= {h.lower() for h in extra_allowed_hosts}
    return polite_http.NetGuardConfig(
        allowed_hosts=frozenset(allowed),
        allow_private=settings.HA_SEED_SOURCE_ALLOW_PRIVATE,
        block_private_ranges=settings.HA_SEED_SOURCE_BLOCK_PRIVATE_RANGES,
    )


def validate_https_url(url: object, *, purpose: str = "uri") -> str:
    """The https-only URL grammar check (structural, pre-DNS).

    Delegates to the vendored family parser
    (:mod:`app.extensions.extension_sources` — the one grammar; this module
    keeps no regex of its own). ``file://``/``http://``/userinfo/whitespace
    forms are refused here with ``URI_INVALID``/``NET_DENIED`` before any
    resolution or subprocess runs; the code is the parser's, the wording
    stays the transport's.
    """
    if not isinstance(url, str) or not url.strip():
        raise SeedTransportError("URI_INVALID", f"{purpose} is required")
    try:
        return extension_sources.parse_url(url)
    except extension_sources.SourceRefError as exc:
        if exc.code == "NET_DENIED":
            message = f"{purpose} scheme is refused — seed sources are https-only"
        elif "\\" in url or any(ch.isspace() for ch in url):
            message = f"{purpose} carries a forbidden character"
        else:
            message = f"{purpose} must be a plain https URL (no userinfo, no query)"
        raise SeedTransportError(exc.code, message) from exc


def _parse_source_ref(ref_spec: str | None) -> extension_sources.ParsedRef:
    """The vendored parser, mapped onto the transport's typed refusal."""
    try:
        return extension_sources.parse_source_ref(ref_spec)
    except extension_sources.SourceRefError as exc:
        raise SeedTransportError(
            exc.code, f"ref {str(ref_spec)[:40]!r} is not a safe git ref"
        ) from exc


def validate_git_ref(ref_spec: str | None) -> str | None:
    """Validate a pinned ref (branch/tag name or 40/64-hex commit).

    ``None``/empty clones the remote's default branch. A leading dash would
    be an argv option injection — refused structurally by the vendored
    family parser.
    """
    return _parse_source_ref(ref_spec).value


def validate_subpath(subpath: str | None) -> str | None:
    """Validate a pack subpath (no traversal, no absolute, no backslash).

    The vendored family grammar: no leading ``/``, no ``..`` segment, no
    backslash and no trailing slash (a trailing separator is refused, not
    trimmed).
    """
    try:
        return extension_sources.parse_subpath(subpath)
    except extension_sources.SourceRefError as exc:
        raise SeedTransportError(exc.code, f"subpath {subpath!r} is not a safe pack path") from exc


async def _check_and_resolve(
    url: str, *, guard: polite_http.NetGuardConfig, resolver: polite_http.Resolver | None
) -> list[str]:
    """The vendored guard, off the event loop; an empty answer fails closed."""
    try:
        resolved = await asyncio.to_thread(
            polite_http.check_url, url, config=guard, resolver=resolver
        )
    except polite_http.PoliteHttpError as exc:
        raise SeedTransportError("NET_DENIED", exc.detail) from exc
    if not resolved:
        host = urlparse(url).hostname or url
        raise SeedTransportError("UNREACHABLE", f"could not resolve {host} — failing closed")
    return resolved


async def fetch_pack(
    source,
    *,
    client: httpx.AsyncClient | None = None,
    resolver: polite_http.Resolver | None = None,
    token: str | None = None,
    if_none_match: str | None = None,
) -> FetchedPack:
    """Fetch one source's current pack (dispatch by ``source.kind``).

    ``token`` overrides the slot resolution (callers that already resolved
    ``auth_secret_ref`` pass it through; the transport never logs it).
    ``client`` is injectable for tests (``httpx.MockTransport``). A
    conditional registry GET that answers 304 comes back with
    ``is_not_modified`` set (no manifest, no files — nothing new).
    """
    from app.models.enums import SeedSourceKind

    kind = source.kind.value if source.kind is not None else ""
    if kind == SeedSourceKind.GIT.value:
        return await fetch_git(
            source.uri,
            source.ref_spec,
            source.subpath,
            token=token,
            resolver=resolver,
        )
    if kind == SeedSourceKind.REGISTRY.value:
        owns_client = client is None
        if owns_client:
            from app.core.config import get_settings

            timeout = get_settings().HA_SEED_FETCH_TIMEOUT_SECONDS
            client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout, connect=min(30.0, float(timeout))),
            )
        try:
            body = await fetch_registry(
                source.uri,
                client=client,
                token=token,
                resolver=resolver,
                if_none_match=if_none_match,
            )
        finally:
            if owns_client:
                await client.aclose()
        if body.not_modified:
            return FetchedPack(
                manifest={}, files={}, etag=body.etag, final_url=body.final_url, not_modified=True
            )
        if not body.body:
            raise SeedTransportError(
                "PACK_NOT_FOUND", "the registry returned an empty body (no pack)"
            )
        manifest, files = split_archive(body.body)
        return FetchedPack(manifest=manifest, files=files, etag=body.etag, final_url=body.final_url)
    raise SeedTransportError(
        "UNSUPPORTED_KIND",
        f"kind {kind!r} does not fetch (bundled boots locally; file sources "
        "receive packs through upload)",
    )


def _url_origin(url: str) -> tuple[str, str, int]:
    """The origin a credential rides within: scheme + host + effective
    port (httpx's redirect rule — a port-less https URL normalizes to
    443; an unparsable port matches nothing, fail closed)."""
    parsed = urlparse(url)
    try:
        port: int | None = parsed.port
    except ValueError:
        return parsed.scheme, parsed.hostname or "", -1
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname or "", port


async def fetch_registry(
    uri: str,
    *,
    client: httpx.AsyncClient,
    token: str | None = None,
    resolver: polite_http.Resolver | None = None,
    if_none_match: str | None = None,
) -> _FetchedBody:
    """Fetch a pack archive body from a registry URL (the guarded GET).

    Every hop re-runs the https-only grammar (a redirect that downgrades
    the scheme is a typed refusal, never followed), is re-guarded and
    pinned, and the body streams under the byte cap. The credential rides
    **same-origin hops only** (scheme + host + port): a redirect to a
    different origin drops the ``Authorization`` header (httpx strips
    cross-origin auth on its own redirects; the manual loop must match),
    so a plaintext same-host hop can never receive it either. A ``304
    Not Modified`` (conditional GET with the latest known etag) returns
    ``not_modified`` — "nothing new", the pin stands; an empty ``200``
    body is a missing pack, not a 304.
    """
    from app.core.config import get_settings

    max_bytes = get_settings().HA_SEED_FETCH_MAX_BYTES
    guard = seed_net_guard()
    url = validate_https_url(uri, purpose="registry uri")
    current = url
    origin = _url_origin(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        current = validate_https_url(current, purpose="redirect target")
        resolved = await _check_and_resolve(current, guard=guard, resolver=resolver)
        request_url, pin_headers, pin_extensions = polite_http.pinned_request_parts(
            current, resolved
        )
        headers = {"Accept": "application/zip, application/octet-stream", **pin_headers}
        if token and _url_origin(current) == origin:
            headers["Authorization"] = f"Bearer {token}"
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        request = client.build_request("GET", request_url, headers=headers)
        if pin_extensions:
            request.extensions.update(pin_extensions)
        try:
            response = await client.send(request)
        except (httpx.RequestError, OSError) as exc:
            raise SeedTransportError(
                "UNREACHABLE", redact_secret(f"network error contacting {current}: {exc}", token)
            ) from exc
        if response.status_code in _REDIRECT_STATUS:
            location = response.headers.get("location")
            if not location:
                raise SeedTransportError("PROVIDER", f"redirect without location from {current}")
            current = urljoin(current, location)
            continue
        if response.status_code in (401, 403):
            raise SeedTransportError(
                "AUTH", f"{urlparse(current).hostname} returned {response.status_code}"
            )
        if response.status_code == 304:
            return _FetchedBody(body=b"", etag=if_none_match, final_url=current, not_modified=True)
        if response.status_code >= 400:
            raise SeedTransportError(
                "PROVIDER", f"{urlparse(current).hostname} returned {response.status_code}"
            )
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise SeedTransportError(
                "FETCH_TOO_LARGE", f"content-length {declared} exceeds the {max_bytes}-byte cap"
            )
        data = bytearray()
        async for chunk in response.aiter_bytes(_CHUNK):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise SeedTransportError(
                    "FETCH_TOO_LARGE", f"response exceeds the {max_bytes}-byte cap"
                )
        return _FetchedBody(body=bytes(data), etag=response.headers.get("etag"), final_url=current)
    raise SeedTransportError("PROVIDER", f"too many redirects (>{_MAX_REDIRECTS})")


async def fetch_git(
    uri: str,
    ref_spec: str | None,
    subpath: str | None,
    *,
    token: str | None = None,
    resolver: polite_http.Resolver | None = None,
) -> FetchedPack:
    """Fetch a pack from a git repository at a pinned ref (shallow, https).

    The ``git`` CLI runs from an argv list (no shell), auth rides the
    environment (``GIT_CONFIG_*``), redirects are disabled, the wall clock
    and the checkout size are capped. The pack is read from ``subpath`` (or
    the repo root) through the pack loader's path-safety rules; the temp
    checkout is removed afterwards.
    """
    from app.core.config import get_settings
    from app.core.seed_pack.manifest import load_pack_dir

    settings = get_settings()
    timeout = settings.HA_SEED_FETCH_TIMEOUT_SECONDS
    max_bytes = settings.HA_SEED_FETCH_MAX_BYTES
    url = validate_https_url(uri, purpose="git uri")
    parsed_ref = _parse_source_ref(ref_spec)
    ref = parsed_ref.value
    clean_subpath = validate_subpath(subpath)
    guard = seed_net_guard()
    await _check_and_resolve(url, guard=guard, resolver=resolver)

    with tempfile.TemporaryDirectory(prefix="seed-source-") as tmp:
        work = Path(tmp) / "repo"
        env = {**os.environ}
        if token:
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
                }
            )
        env.pop("GIT_ASKPASS", None)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if ref is not None and parsed_ref.is_commit_pin:
            await _run_git(["init", "--quiet", str(work)], env=env, timeout=timeout)
            await _run_git(
                ["-C", str(work), "remote", "add", "origin", url], env=env, timeout=timeout
            )
            # `-c` config precedes the subcommand: redirects are disabled on
            # the sha fetch exactly as on the clone, so the checked host is
            # the connected host.
            await _run_git(
                [
                    "-C",
                    str(work),
                    "-c",
                    "http.followRedirects=false",
                    "fetch",
                    "--depth",
                    "1",
                    "--no-tags",
                    "origin",
                    ref,
                ],
                env=env,
                timeout=timeout,
            )
            await _run_git(
                ["-C", str(work), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
                env=env,
                timeout=timeout,
            )
        else:
            args = ["clone", "--depth", "1", "--no-tags", "-c", "http.followRedirects=false"]
            if ref is not None:
                args += ["--branch", ref, "--single-branch"]
            args += ["--", url, str(work)]
            await _run_git(args, env=env, timeout=timeout)
        sha = (
            (await _run_git(["-C", str(work), "rev-parse", "HEAD"], env=env, timeout=timeout))
            .decode("utf-8", "replace")
            .strip()
        )
        total = 0
        for path in work.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                if total > max_bytes:
                    raise SeedTransportError(
                        "FETCH_TOO_LARGE", f"checkout exceeds the {max_bytes}-byte cap"
                    )
        pack_dir = work / clean_subpath if clean_subpath else work
        try:
            manifest, files, _errors = load_pack_dir(pack_dir)
        except FileNotFoundError as exc:
            raise SeedTransportError(
                "PACK_NOT_FOUND",
                f"no manifest.json at {clean_subpath or 'the repository root'}",
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SeedTransportError("ARCHIVE_INVALID", "manifest.json is not valid JSON") from exc
        if not manifest:
            raise SeedTransportError("PACK_NOT_FOUND", "manifest.json is empty")
        return FetchedPack(manifest=manifest, files=files, commit_sha=sha or None, final_url=url)


async def _run_git(args: list[str], *, env: dict, timeout: int) -> bytes:
    """One git invocation (argv list, no shell) under the wall-clock cap.

    Stderr is redacted (the token never reaches a message) and capped.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise SeedTransportError("FETCH_TIMEOUT", f"git timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = redact_secret(
            stderr.decode("utf-8", "replace").strip() or f"git exited {proc.returncode}",
            env.get("GIT_CONFIG_VALUE_0", "").removeprefix("Authorization: Bearer ") or None,
        )
        raise SeedTransportError("UNREACHABLE", detail)
    return stdout


def pack_files_from_zip(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
) -> dict[str, bytes]:
    """Extract a pack archive's files (the upload/registry zip shape).

    The entry allowlist is ``manifest.json`` + ``items/**.json``; traversal
    (``..`` segments), absolute paths and backslashes are typed refusals
    before anything is read, and the expansion guard bounds both a single
    entry and the whole archive (the ``unpack_seeds_zip`` rules — a curdled
    or hostile archive never writes past the pack namespace). The returned
    map includes ``manifest.json``; use :func:`split_archive` (or
    :func:`build_pack_from_zip`) to separate it for ``parse_pack``.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total = 0
            for info in zf.infolist():
                if info.file_size > max_entry_bytes:
                    raise SeedTransportError(
                        "ARCHIVE_TOO_LARGE",
                        f"entry {info.filename!r} exceeds the {max_entry_bytes}-byte cap",
                    )
                total += info.file_size
                if total > max_total_bytes:
                    raise SeedTransportError(
                        "ARCHIVE_TOO_LARGE",
                        f"archive expands beyond the {max_total_bytes}-byte cap",
                    )
            files: dict[str, bytes] = {}
            for info in zf.infolist():
                name = info.filename
                try:
                    entry = extension_sources.validate_archive_entry(name)
                except extension_sources.SourceRefError as exc:
                    if exc.code == "ARCHIVE_PATH_TRAVERSAL":
                        raise SeedTransportError(exc.code, name) from exc
                    raise SeedTransportError(
                        exc.code, f"{name!r} is not manifest.json or items/*.json"
                    ) from exc
                if entry.endswith("/"):
                    continue
                if len(files) >= extension_sources.MAX_ARCHIVE_ENTRIES:
                    raise SeedTransportError(
                        "ARCHIVE_TOO_LARGE",
                        f"more than {extension_sources.MAX_ARCHIVE_ENTRIES} entries",
                    )
                files[entry] = zf.read(info)
            if "manifest.json" not in files:
                raise SeedTransportError("PACK_NOT_FOUND", "archive has no manifest.json")
            return files
    except zipfile.BadZipFile as exc:
        raise SeedTransportError("ARCHIVE_INVALID", "content is not a valid zip archive") from exc


def split_archive(
    data: bytes, *, max_entry_bytes: int | None = None, max_total_bytes: int | None = None
) -> tuple[dict, dict[str, bytes]]:
    """Extract + split an archive into ``(manifest, item_files)`` — the
    ``parse_pack`` input shape (the manifest decoded, item files only)."""
    from app.core.seed_pack.manifest import MAX_ITEM_FILE_BYTES, MAX_PACK_TOTAL_BYTES

    files = pack_files_from_zip(
        data,
        max_entry_bytes=max_entry_bytes or MAX_ITEM_FILE_BYTES,
        max_total_bytes=max_total_bytes or MAX_PACK_TOTAL_BYTES,
    )
    manifest_bytes = files.pop("manifest.json")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SeedTransportError("ARCHIVE_INVALID", "manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict) or not manifest:
        raise SeedTransportError("ARCHIVE_INVALID", "manifest.json must be a JSON object")
    return manifest, files


def build_pack_from_zip(
    data: bytes, *, max_entry_bytes: int | None = None, max_total_bytes: int | None = None
) -> tuple:
    """Unpack + validate an archive into ``(manifest, files, ParsedPack|None, errors)``.

    The parse runs through the same pack contract as every other lane
    (:func:`app.core.seed_pack.manifest.parse_pack` — digest pins, caps,
    coverage matrix); ``errors`` carries the typed :class:`PackError` list
    when the pack does not validate.
    """
    from app.core.seed_pack.manifest import parse_pack

    manifest, files = split_archive(
        data, max_entry_bytes=max_entry_bytes, max_total_bytes=max_total_bytes
    )
    parsed, errors = parse_pack(manifest, files)
    return manifest, files, parsed, errors


def build_pack_from_single_json(
    filename: str,
    content: bytes,
    *,
    pack_id: str,
    version: str,
    license_id: str,
    scope: str,
    priority: int,
) -> tuple:
    """Build a one-item-file pack from a single uploaded JSON envelope.

    The file's basename must be a registered pack item filename
    (``medications.json`` …) — the coverage matrix classifies the upload;
    an unknown name is a typed refusal, never a guess. The manifest carries
    the source's scope/priority (coherence is pinned at registration) and
    the same validation runs as for every pack. Returns
    ``(manifest, files, ParsedPack|None, errors)``.
    """
    from app.core.seed_pack.items import BY_FILENAME
    from app.core.seed_pack.manifest import (
        MAX_ITEM_FILE_BYTES,
        PACK_SCHEMA_VERSION,
        PackError,
        content_digest,
        parse_pack,
    )

    basename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    if basename not in BY_FILENAME:
        return (
            None,
            {},
            None,
            [
                PackError(
                    "ITEM_TYPE_UNKNOWN",
                    basename,
                    "upload a registered pack item filename (e.g. medications.json) "
                    "or a full pack zip (manifest.json + items/)",
                )
            ],
        )
    if len(content) > MAX_ITEM_FILE_BYTES:
        return (
            None,
            {},
            None,
            [
                PackError(
                    "ITEM_FILE_TOO_LARGE",
                    basename,
                    f"{len(content)} bytes exceeds the {MAX_ITEM_FILE_BYTES} cap",
                )
            ],
        )
    path = f"items/{basename}"
    manifest = {
        "id": pack_id,
        "schema_version": PACK_SCHEMA_VERSION,
        "version": version,
        "license": license_id,
        "scope": scope,
        "priority": priority,
        "item_files": [{"path": path, "sha256": content_digest(content)}],
    }
    files = {path: content}
    parsed, errors = parse_pack(manifest, files)
    return manifest, files, parsed, errors

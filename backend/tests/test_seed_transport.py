"""The seed source transport — SSRF guard, pins, limits, archives, slots
(ADR 0056 Phase 3).

Pinned behaviors:

* the URL grammar is https-only: ``file://``/``http://``/``ssh://`` and
  userinfo credentials are structurally refused before any DNS or
  subprocess runs;
* the vendored SSRF guard refuses loopback/private/numeric-literal targets
  (an allowlisted host is the documented exception), and a resolution
  failure during a fetch fails closed;
* the registry GET re-runs the https-only grammar on every redirect hop
  (a scheme-downgrading hop is refused before it is requested), re-guards
  each hop with the address pinned into the request (Host header keeps
  the name), rides the credential on same-origin hops only
  (scheme+host+port), captures the etag, answers 304 as "nothing new",
  refuses 401/403 as ``AUTH`` and enforces the byte cap while streaming;
* git refs/subpaths are bounded grammars (no leading dash — an argv option
  injection is structurally impossible; no ``..`` traversal); the clone is
  shallow with redirects disabled, and a commit pin fetches the sha (never
  a branch clone);
* archives run the ``unpack_seeds_zip`` rules: ``manifest.json`` +
  ``items/**.json`` only, traversal/backslash/absolute names and oversized
  expansions are typed refusals;
* a single uploaded JSON wraps into a manifest carrying the source's
  scope/priority; an unregistered filename is a typed refusal;
* secret slots resolve keyring-first then the ``HA_SEED_SOURCE_SECRET_*``
  env fallback, and a resolved secret is redacted from rendered errors.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import tempfile
import zipfile
from unittest.mock import patch

import httpx
import pytest
from nx_auth.secrets import redact_secret

from app.core.config import get_settings
from app.extensions import extension_sources
from app.services.seed import transport
from app.services.seed.secrets import env_var_for_slot, resolve_secret_slot
from app.services.seed.transport import (
    SeedTransportError,
    build_pack_from_single_json,
    pack_files_from_zip,
    split_archive,
    validate_git_ref,
    validate_https_url,
    validate_subpath,
)

_RESOLVED = lambda host: ["1.2.3.4"]  # noqa: E731 -- injected resolver (no real DNS in tests)


def _pack_zip(
    items: dict[str, list[dict]] | None = None,
    *,
    manifest_extra: dict | None = None,
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    items = (
        items
        if items is not None
        else {"medications.json": [{"seed_key": "medication:t", "name": "T"}]}
    )
    item_files = []
    files: dict[str, bytes] = {}
    for filename, rows in items.items():
        content = json.dumps(
            {"metadata": {"version": "1.0.0"}, "items": rows},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        files[f"items/{filename}"] = content
        item_files.append(
            {"path": f"items/{filename}", "sha256": hashlib.sha256(content).hexdigest()}
        )
    manifest = {
        "id": "transport-test-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "CC0-1.0",
        "scope": "system",
        "priority": 100,
        "item_files": item_files,
    }
    manifest.update(manifest_extra or {})
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    files.update(extra_entries or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


class _FakeSource:
    """A registry-kind source stand-in (kind/uri are all the transport reads)."""

    kind_value = "registry"

    def __init__(self, uri: str):
        self.uri = uri

    @property
    def kind(self):
        class _K:
            value = "registry"

        return _K()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


# -- URL / ref / subpath grammars ---------------------------------------------


def test_file_and_insecure_schemes_are_refused():
    for url in (
        "file:///etc/seeds",
        "http://example.com/pack.zip",
        "ssh://git@example.com/repo",
    ):
        with pytest.raises(SeedTransportError):
            validate_https_url(url)


def test_userinfo_and_whitespace_urls_are_refused():
    with pytest.raises(SeedTransportError):
        validate_https_url("https://user:token@example.com/repo.git")
    with pytest.raises(SeedTransportError):
        validate_https_url("https://example.com/a b.zip")


def test_git_ref_grammar_refuses_option_injection():
    assert validate_git_ref("v1.2.3") == "v1.2.3"
    assert validate_git_ref("main") == "main"
    assert validate_git_ref("3f9a" * 10) == "3f9a" * 10
    with pytest.raises(SeedTransportError):
        validate_git_ref("--upload-pack=evil")
    with pytest.raises(SeedTransportError):
        validate_git_ref("main extra")
    assert validate_git_ref("  ") is None
    assert validate_git_ref(None) is None


def test_subpath_grammar_refuses_traversal():
    assert validate_subpath("packs/core") == "packs/core"
    with pytest.raises(SeedTransportError):
        validate_subpath("../etc")
    with pytest.raises(SeedTransportError):
        validate_subpath("/absolute")
    with pytest.raises(SeedTransportError):
        validate_subpath("a\\b")


# -- the vendored family grammar is the only grammar (plan 60 D4) --------------


def test_transport_keeps_no_source_grammar_of_its_own():
    """The vendored parser is the one grammar: no local regexes, no local cap."""
    for name in (
        "HTTPS_URL_RE",
        "GIT_REF_RE",
        "COMMIT_SHA_RE",
        "SUBPATH_RE",
        "MAX_ARCHIVE_ENTRIES",
    ):
        assert not hasattr(transport, name), f"{name} must come from extension_sources"
    source = inspect.getsource(transport)
    assert "re.compile" not in source
    assert "\nimport re\n" not in source


def test_url_grammar_is_the_vendored_parser():
    valid = (
        "https://git.example.com/ext/greenhouse-jobs",
        "https://git.example.com:8443/ext/repo",
        "https://git.example.com/a~b!$&'()*+,;=:%2F-_./c",
    )
    for url in valid:
        assert validate_https_url(url) == url
        assert extension_sources.parse_url(url) == url
    hostile = {
        "https://user:ghp_leakme@git.example.com/ext/repo": "URI_INVALID",
        "http://git.example.com/ext/repo": "URI_INVALID",
        "file:///etc/seeds": "NET_DENIED",
        "ftp://example.com/pack.zip": "NET_DENIED",
        "ssh://git@example.com/repo": "NET_DENIED",
        "git://example.com/repo": "NET_DENIED",
        "https://git.example.com/ext/repo?token=ghp_leakme": "URI_INVALID",
        "https://git.example.com/ext/repo#main": "URI_INVALID",
        "https://git.example.com/ext\\repo": "URI_INVALID",
        "https://git.example.com/ext repo": "URI_INVALID",
        "https://git.example.com/ext/repo ": "URI_INVALID",
        "https://git.example.com/" + "a" * 500: "URI_INVALID",
        "https://x": "URI_INVALID",
        "": "URI_INVALID",
    }
    for url, code in hostile.items():
        with pytest.raises(SeedTransportError) as excinfo:
            validate_https_url(url)
        assert excinfo.value.code == code
        assert "ghp_leakme" not in str(excinfo.value), "the refused URL never reaches the message"
        with pytest.raises(extension_sources.SourceRefError) as ref_exc:
            extension_sources.parse_url(url)
        assert ref_exc.value.code == code


def test_ref_grammar_is_the_vendored_parser():
    assert validate_git_ref("main") == extension_sources.parse_source_ref("main").value
    assert validate_git_ref("v1.2.3") == "v1.2.3"
    assert validate_git_ref("3f9a" * 10) == "3f9a" * 10
    assert validate_git_ref("  ") is None
    assert validate_git_ref(None) is None
    for bad in ("--upload-pack=evil", "main extra", 'ma"in', "a" * 201):
        with pytest.raises(SeedTransportError) as excinfo:
            validate_git_ref(bad)
        assert excinfo.value.code == "REF_INVALID"
        with pytest.raises(extension_sources.SourceRefError) as ref_exc:
            extension_sources.parse_source_ref(bad)
        assert ref_exc.value.code == "REF_INVALID"


def test_strategy_commit_pin_is_detected_for_the_sha_fetch():
    sha = "a" * 40
    pinned = extension_sources.parse_source_ref(sha, strategy="commit-pin")
    assert pinned.is_commit_pin
    assert pinned.commit_sha == sha
    assert pinned.strategy == "commit-pin"
    detected = transport._parse_source_ref(sha)
    assert detected.is_commit_pin, "a bare 40-hex ref selects the sha fetch"
    assert detected.strategy is None
    # Uppercase hex is a BRANCH name bare, and a typed refusal as a pin.
    upper = "A" * 40
    assert not transport._parse_source_ref(upper).is_commit_pin
    with pytest.raises(extension_sources.SourceRefError) as excinfo:
        extension_sources.parse_source_ref(upper, strategy="commit-pin")
    assert excinfo.value.code == "REF_INVALID"


def test_subpath_grammar_is_the_vendored_parser():
    assert validate_subpath("packs/core") == "packs/core"
    assert validate_subpath(None) is None
    assert validate_subpath("   ") is None
    for bad in ("../etc", "/absolute", "a\\b", "packs/core/", "packs/./core", "a/" * 128):
        with pytest.raises(SeedTransportError) as excinfo:
            validate_subpath(bad)
        assert excinfo.value.code == "SUBPATH_INVALID"
        with pytest.raises(extension_sources.SourceRefError) as ref_exc:
            extension_sources.parse_subpath(bad)
        assert ref_exc.value.code == "SUBPATH_INVALID"


# -- archive rules (the unpack_seeds_zip rules) --------------------------------


def test_pack_zip_round_trips_through_the_guard():
    data = _pack_zip()
    files = pack_files_from_zip(
        data, max_entry_bytes=5 * 1024 * 1024, max_total_bytes=64 * 1024 * 1024
    )
    assert "manifest.json" in files
    assert "items/medications.json" in files
    manifest, item_files = split_archive(data)
    assert manifest["id"] == "transport-test-pack"
    assert set(item_files) == {"items/medications.json"}


def test_archive_refuses_unknown_entries():
    data = _pack_zip(extra_entries={"run.sh": b"#!/bin/sh\n"})
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(data, max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo.value.code == "ARCHIVE_ENTRY_FORBIDDEN"


def test_archive_refuses_path_traversal_and_backslashes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("items/../../etc/passwd.json", b"{}")
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(buffer.getvalue(), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo.value.code == "ARCHIVE_PATH_TRAVERSAL"

    buffer2 = io.BytesIO()
    with zipfile.ZipFile(buffer2, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("items\\evil.json", b"{}")
    with pytest.raises(SeedTransportError) as excinfo2:
        pack_files_from_zip(buffer2.getvalue(), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo2.value.code == "ARCHIVE_PATH_TRAVERSAL"


def test_archive_refuses_oversized_expansion():
    data = _pack_zip()
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(data, max_entry_bytes=10, max_total_bytes=100)
    assert excinfo.value.code == "ARCHIVE_TOO_LARGE"


def test_archive_without_manifest_is_typed():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("items/medications.json", b"{}")
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(buffer.getvalue(), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo.value.code == "PACK_NOT_FOUND"


def test_archive_entry_allowlist_is_the_vendored_parser():
    for name, expected in (
        ("manifest.json", "manifest.json"),
        ("items/medications.json", "items/medications.json"),
        ("items/2026/medications.json", "items/2026/medications.json"),
        ("items/", "items/"),
    ):
        assert extension_sources.validate_archive_entry(name) == expected
    for name, code in (
        ("items/x.txt", "ARCHIVE_ENTRY_FORBIDDEN"),
        ("run.sh", "ARCHIVE_ENTRY_FORBIDDEN"),
        (".env", "ARCHIVE_ENTRY_FORBIDDEN"),
        ("packs/manifest.json", "ARCHIVE_ENTRY_FORBIDDEN"),
        ("items/../../etc/passwd.json", "ARCHIVE_PATH_TRAVERSAL"),
        ("items/../manifest.json", "ARCHIVE_PATH_TRAVERSAL"),
        ("/etc/passwd.json", "ARCHIVE_PATH_TRAVERSAL"),
        ("items\\medications.json", "ARCHIVE_PATH_TRAVERSAL"),
    ):
        with pytest.raises(extension_sources.SourceRefError) as excinfo:
            extension_sources.validate_archive_entry(name)
        assert excinfo.value.code == code


def test_pack_zip_accepts_nested_items_and_skips_directories():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", b"{}")
        zf.writestr("items/", b"")
        zf.writestr("packs/", b"")
        zf.writestr("items/2026/medications.json", b"{}")
    files = pack_files_from_zip(
        buffer.getvalue(), max_entry_bytes=1024, max_total_bytes=1024 * 1024
    )
    assert set(files) == {"manifest.json", "items/2026/medications.json"}


def test_archive_directory_traversal_is_still_refused():
    """A hostile DIRECTORY entry is a traversal refusal, not a silently
    skipped entry (the vendored parser checks traversal before the
    directory allowance)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", b"{}")
        zf.writestr("../", b"")
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(buffer.getvalue(), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo.value.code == "ARCHIVE_PATH_TRAVERSAL"


def test_archive_entry_cap_is_the_vendored_constant():
    assert extension_sources.MAX_ARCHIVE_ENTRIES == 512

    def _archive(item_count: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("manifest.json", b"{}")
            for index in range(item_count):
                zf.writestr(f"items/item-{index}.json", b"{}")
        return buffer.getvalue()

    boundary = pack_files_from_zip(_archive(511), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert len(boundary) == extension_sources.MAX_ARCHIVE_ENTRIES
    with pytest.raises(SeedTransportError) as excinfo:
        pack_files_from_zip(_archive(512), max_entry_bytes=1024, max_total_bytes=1024 * 1024)
    assert excinfo.value.code == "ARCHIVE_TOO_LARGE"


# -- single-JSON uploads --------------------------------------------------------


def test_single_json_upload_wraps_into_a_coherent_manifest():
    content = json.dumps(
        {"metadata": {"version": "1.0.0"}, "items": [{"seed_key": "medication:u", "name": "U"}]}
    ).encode("utf-8")
    manifest, _files, parsed, errors = build_pack_from_single_json(
        "medications.json",
        content,
        pack_id="upload-abc12345",
        version="1.0.0",
        license_id="CC0-1.0",
        scope="tenant",
        priority=250,
    )
    assert errors == []
    assert parsed is not None
    assert manifest["scope"] == "tenant"
    assert manifest["priority"] == 250
    assert parsed.manifest["priority"] == 250


def test_single_json_upload_refuses_unknown_filenames():
    content = b'{"metadata": {}, "items": []}'
    _manifest, _files, parsed, errors = build_pack_from_single_json(
        "evil.json",
        content,
        pack_id="upload-abc12345",
        version="1.0.0",
        license_id="CC0-1.0",
        scope="system",
        priority=100,
    )
    assert parsed is None
    assert errors and errors[0].code == "ITEM_TYPE_UNKNOWN"


# -- registry fetch: guard, pins, caps ------------------------------------------


async def test_registry_fetch_happy_path_pins_etag():
    data = _pack_zip()
    calls: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["host"] = request.headers.get("host")
        calls["auth"] = request.headers.get("authorization")
        calls["inm"] = request.headers.get("if-none-match")
        return httpx.Response(
            200, headers={"etag": '"W/abc"', "content-length": str(len(data))}, content=data
        )

    client = _client(handler)
    fetched = await transport.fetch_pack(
        _FakeSource("https://registry.example.com/pack.zip"),
        client=client,
        token="sekrit",
        if_none_match='"W/old"',
        resolver=_RESOLVED,
    )
    await client.aclose()
    assert calls["auth"] == "Bearer sekrit"
    assert calls["inm"] == '"W/old"'
    assert calls["host"] == "registry.example.com", "the pin keeps the original Host header"
    assert fetched.etag == '"W/abc"'
    assert "items/medications.json" in fetched.files
    assert fetched.manifest["id"] == "transport-test-pack"


async def test_registry_fetch_304_is_nothing_new():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    client = _client(handler)
    fetched = await transport.fetch_pack(
        _FakeSource("https://registry.example.com/pack.zip"),
        client=client,
        if_none_match='"W/abc"',
        resolver=_RESOLVED,
    )
    await client.aclose()
    assert fetched.is_not_modified
    assert fetched.etag == '"W/abc"'


async def test_registry_refuses_auth_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), client=client, resolver=_RESOLVED
        )
    await client.aclose()
    assert excinfo.value.code == "AUTH"


async def test_registry_byte_cap_enforced_while_streaming(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (64 * 1024))

    monkeypatch.setattr(get_settings(), "HA_SEED_FETCH_MAX_BYTES", 1024)
    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), client=client, resolver=_RESOLVED
        )
    await client.aclose()
    assert excinfo.value.code == "FETCH_TOO_LARGE"


async def test_registry_content_length_cap_refuses_before_reading(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(10 * 1024 * 1024)}, content=b"")

    monkeypatch.setattr(get_settings(), "HA_SEED_FETCH_MAX_BYTES", 1024)
    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), client=client, resolver=_RESOLVED
        )
    await client.aclose()
    assert excinfo.value.code == "FETCH_TOO_LARGE"


async def test_registry_redirect_to_private_target_is_refused():
    """A redirect that lands on a private target fails loudly — never followed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("host") == "registry.example.com":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/pack.zip"})
        raise AssertionError("the private hop must never be requested")

    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), client=client, resolver=_RESOLVED
        )
    await client.aclose()
    assert excinfo.value.code == "NET_DENIED"


async def test_registry_cross_host_redirect_drops_the_token():
    """A credential rides same-host hops only: a redirect to another host
    never carries the Authorization header (httpx strips cross-origin auth
    on its own redirects — the manual loop must match)."""
    seen: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("host"), request.headers.get("authorization")))
        if request.headers.get("host") == "registry.example.com":
            return httpx.Response(302, headers={"location": "https://mirror.example.net/pack.zip"})
        return httpx.Response(200, content=_pack_zip())

    client = _client(handler)
    fetched = await transport.fetch_pack(
        _FakeSource("https://registry.example.com/pack.zip"),
        client=client,
        token="sekrit-tok",
        resolver=_RESOLVED,
    )
    await client.aclose()
    assert seen[0] == ("registry.example.com", "Bearer sekrit-tok"), (
        "the first hop carries the credential"
    )
    assert seen[1][0] == "mirror.example.net"
    assert seen[1][1] is None, "the token never follows a cross-host redirect"
    assert "items/medications.json" in fetched.files


async def test_registry_same_host_redirect_keeps_the_token():
    """A same-host redirect (path change only) still carries the credential."""

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.headers.get("host") == "registry.example.com"
            and request.url.path == "/pack.zip"
        ):
            return httpx.Response(
                302, headers={"location": "https://registry.example.com/v2/pack.zip"}
            )
        assert request.headers.get("authorization") == "Bearer sekrit-tok"
        return httpx.Response(200, content=_pack_zip())

    client = _client(handler)
    fetched = await transport.fetch_pack(
        _FakeSource("https://registry.example.com/pack.zip"),
        client=client,
        token="sekrit-tok",
        resolver=_RESOLVED,
    )
    await client.aclose()
    assert "items/medications.json" in fetched.files


async def test_registry_empty_200_is_not_a_304():
    """An empty 200 body is a missing pack, not "nothing new" — the sync
    must never read it as up-to-date."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), client=client, resolver=_RESOLVED
        )
    await client.aclose()
    assert excinfo.value.code == "PACK_NOT_FOUND"


async def test_registry_loopback_literal_is_net_denied():
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(_FakeSource("https://127.0.0.1/pack.zip"))
    assert excinfo.value.code == "NET_DENIED"


async def test_registry_decimal_literal_host_is_net_denied():
    """The numeric-literal obfuscations (2130706433 = 127.0.0.1) are coerced
    and checked before any DNS reliance."""
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(_FakeSource("https://2130706433/pack.zip"))
    assert excinfo.value.code == "NET_DENIED"


async def test_registry_resolution_failure_fails_closed():
    def _no_resolution(host: str) -> list[str]:
        return []

    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"), resolver=_no_resolution
        )
    assert excinfo.value.code == "UNREACHABLE"


async def test_allowlisted_private_host_is_permitted(monkeypatch):
    data = _pack_zip()
    monkeypatch.setattr(get_settings(), "HA_SEED_SOURCE_ALLOWED_HOSTS", "seedbox.internal")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.headers.get("host")
        return httpx.Response(200, content=data)

    client = _client(handler)
    with patch("socket.getaddrinfo", return_value=[(None, None, None, "", ("10.0.0.9", 443))]):
        fetched = await transport.fetch_pack(
            _FakeSource("https://seedbox.internal/pack.zip"), client=client
        )
    await client.aclose()
    assert "items/medications.json" in fetched.files
    assert captured["host"] == "seedbox.internal"


async def test_secret_is_never_in_a_rendered_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"")

    client = _client(handler)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_pack(
            _FakeSource("https://registry.example.com/pack.zip"),
            client=client,
            token="sekrit-tok",
            resolver=_RESOLVED,
        )
    await client.aclose()
    assert "sekrit-tok" not in str(excinfo.value)


# -- git transport: argv shape and pinning ---------------------------------------


class _FakeTempDir:
    """A TemporaryDirectory stand-in whose `repo/` is pre-seeded by the test."""

    def __init__(self, base):
        self.name = str(base)

    def __enter__(self):
        return self.name

    def __exit__(self, *exc):
        return False


def _patch_tempdir(monkeypatch, base):
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda prefix=None: _FakeTempDir(base))


async def test_git_fetch_builds_shallow_pinned_argv(monkeypatch, tmp_path):
    """The clone runs shallow with redirects disabled and a `--` separator
    before the URL; the pack is read from the checkout."""
    commands: list[list[str]] = []

    async def fake_run_git(args, *, env, timeout):
        commands.append(list(args))
        if args[:1] == ["clone"]:
            repo = tmp_path / "repo"
            (repo / "items").mkdir(parents=True)
            (repo / "manifest.json").write_text(json.dumps({"id": "git-pack"}), encoding="utf-8")
            (repo / "items" / "medications.json").write_text(
                json.dumps({"metadata": {"version": "1"}, "items": []}), encoding="utf-8"
            )
        if "rev-parse" in args:
            return b"3f9a" * 10 + b"\n"
        return b""

    monkeypatch.setattr(transport, "_run_git", fake_run_git)
    _patch_tempdir(monkeypatch, tmp_path)

    fetched = await transport.fetch_git(
        "https://git.example.com/seeds.git",
        "v1.2.3",
        None,
        token="t0ken",
        resolver=_RESOLVED,
    )
    clone = commands[0]
    assert clone[0] == "clone"
    assert "--depth" in clone and "1" in clone
    assert "--branch" in clone and clone[clone.index("--branch") + 1] == "v1.2.3"
    assert "--single-branch" in clone
    assert "-c" in clone and "http.followRedirects=false" in clone
    assert clone[-3] == "--"
    assert clone[-2] == "https://git.example.com/seeds.git"
    assert fetched.commit_sha == "3f9a" * 10
    assert fetched.manifest == {"id": "git-pack"}


async def test_git_commit_pin_fetches_the_sha(monkeypatch, tmp_path):
    commands: list[list[str]] = []
    sha = "b" * 40

    async def fake_run_git(args, *, env, timeout):
        commands.append(list(args))
        if "fetch" in args:
            repo = tmp_path / "repo"
            (repo / "items").mkdir(parents=True, exist_ok=True)
            (repo / "manifest.json").write_text(json.dumps({"id": "pin-pack"}), encoding="utf-8")
            (repo / "items" / "medications.json").write_text(
                json.dumps({"metadata": {"version": "1"}, "items": []}), encoding="utf-8"
            )
        if "rev-parse" in args:
            return sha.encode() + b"\n"
        return b""

    monkeypatch.setattr(transport, "_run_git", fake_run_git)
    _patch_tempdir(monkeypatch, tmp_path)

    fetched = await transport.fetch_git(
        "https://git.example.com/s.git", sha, None, resolver=_RESOLVED
    )
    fetch_cmd = next(c for c in commands if "fetch" in c)
    assert sha in fetch_cmd
    assert "--depth" in fetch_cmd
    assert "http.followRedirects=false" in fetch_cmd, (
        "the sha fetch disables redirects exactly like the clone"
    )
    assert fetch_cmd.index("-c") < fetch_cmd.index("fetch"), (
        "the `-c` config precedes the subcommand (git's argv grammar)"
    )
    assert not [c for c in commands if c and c[0] == "clone"], (
        "a commit pin fetches the sha, it never clones a branch"
    )
    assert fetched.commit_sha == sha


async def test_git_bad_ref_fails_before_any_subprocess(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("no subprocess may run for an invalid ref")

    monkeypatch.setattr(transport, "_run_git", fail)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_git("https://git.example.com/s.git", "-evil", None)
    assert excinfo.value.code == "REF_INVALID"


async def test_git_non_manifest_checkout_is_pack_not_found(monkeypatch, tmp_path):
    async def fake_run_git(args, *, env, timeout):
        if args[:1] == ["clone"]:
            (tmp_path / "repo").mkdir(parents=True)
            (tmp_path / "repo" / "README.md").write_text("x", encoding="utf-8")
        if "rev-parse" in args:
            return b"a" * 40 + b"\n"
        return b""

    monkeypatch.setattr(transport, "_run_git", fake_run_git)
    _patch_tempdir(monkeypatch, tmp_path)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport.fetch_git("https://git.example.com/s.git", "main", None, resolver=_RESOLVED)
    assert excinfo.value.code == "PACK_NOT_FOUND"


async def test_git_subpath_selects_the_pack_dir(monkeypatch, tmp_path):
    async def fake_run_git(args, *, env, timeout):
        if args[:1] == ["clone"]:
            repo = tmp_path / "repo"
            (repo / "packs" / "core").mkdir(parents=True)
            (repo / "packs" / "core" / "manifest.json").write_text(
                json.dumps({"id": "sub-pack"}), encoding="utf-8"
            )
        if "rev-parse" in args:
            return b"c" * 40 + b"\n"
        return b""

    monkeypatch.setattr(transport, "_run_git", fake_run_git)
    _patch_tempdir(monkeypatch, tmp_path)
    fetched = await transport.fetch_git(
        "https://git.example.com/s.git", "main", "packs/core", resolver=_RESOLVED
    )
    assert fetched.manifest == {"id": "sub-pack"}


async def test_git_timeout_kills_and_is_typed(monkeypatch):
    killed: list[str] = []

    class _Proc:
        returncode = 0

        def kill(self):
            killed.append("kill")

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return _Proc()

    async def fake_wait_for(coro, timeout):
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    with pytest.raises(SeedTransportError) as excinfo:
        await transport._run_git(["clone", "x"], env={}, timeout=5)
    assert excinfo.value.code == "FETCH_TIMEOUT"
    assert killed == ["kill"], "a timed-out git process is killed, not leaked"


async def test_git_failure_redacts_the_token(monkeypatch):
    class _Proc:
        returncode = 128

        async def communicate(self):
            return b"", b"remote: Unauthorized for Bearer sekrit-tok"

    async def fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    env = {"GIT_CONFIG_VALUE_0": "Authorization: Bearer sekrit-tok"}
    with pytest.raises(SeedTransportError) as excinfo:
        await transport._run_git(["clone", "x"], env=env, timeout=30)
    assert "sekrit-tok" not in str(excinfo.value)


# -- secret slots ----------------------------------------------------------------


def test_secret_slot_env_fallback(monkeypatch):
    monkeypatch.delenv(env_var_for_slot("github-read"), raising=False)
    assert resolve_secret_slot("github-read") is None
    monkeypatch.setenv(env_var_for_slot("github-read"), "tok-123")
    assert resolve_secret_slot("github-read") == "tok-123"
    assert resolve_secret_slot("  github-read ") == "tok-123"
    assert resolve_secret_slot("") is None
    assert resolve_secret_slot(None) is None


def test_redact_secret_masks_and_caps():
    text = "remote: https://user:tok-123@example.com/repo.git unauthorized"
    assert "tok-123" not in redact_secret(text, "tok-123")
    assert len(redact_secret("x" * 1000, None)) == 400

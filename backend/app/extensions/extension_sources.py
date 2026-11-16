# VENDORED FILE — family canonical (ADR-0031 sync-and-gate; plan 60 D4).
# Vendored byte-identically into the three Python hosts (career, health,
# study) at ``backend/app/extensions/extension_sources.py`` by
# ``scripts/sync-extension-contract.sh`` and drift-probed by
# ``scripts/verify-wiring.sh``; the shared fixture group
# (``extension-contract.fixtures.json`` → ``sourceGrammar``) is the
# acceptance wall. NEVER edit the vendored copy in-repo — change this
# canonical, then re-run the sync.
"""The canonical family source grammar (plan 60 D4 / Phase 3).

One parser for the strings an extension source descriptor
(``extension-sources.schema.json``) carries into a host: the https-only
``url``, the resolved ``ref`` value, the ``subpath`` inside a checkout,
the 40/64-hex ``commit-pin``, and the archive entry allowlist/caps a pack
upload must obey. The schema **stays the law** — this module is the
executable mirror of its patterns plus the two rules JSON Schema cannot
express (consumer-visible error codes, and the archive allowlist).

**Strategy ownership stays host-side.** ``$defs.refStrategy`` names four
strategies (``release-tag``, ``tag-pattern``, ``branch``, ``commit-pin``).
This module validates the *shape* of a (strategy, value) pair and answers
"is this value a commit sha?" — it never resolves ``release-tag`` to a
concrete tag, never lists tags, never talks to a git remote. Resolution,
discovery and pin recording are the host's import pipeline (D13: resolve
→ fetch → discover → validate → consent → pin).

**Archive extraction stays app-side.** :func:`validate_archive_entry`
returns the allowlisted entry *name* and refuses traversal/backslash/
absolute forms; the zip reader, the decompression guard, the byte caps and
the write into a quarantine directory are the host app's job (health:
``services/seed/transport.py``'s ``pack_files_from_zip``). Only the
allowlist and the entry-count cap are family law, because every host that
accepts a pack archive must agree on which entries exist at all.

**Trimming is a bare-ref affordance.** A bare ref is stripped before
parsing (health parity); the strategy-bound ``release-tag``/``commit-pin``
paths never trim, so an all-whitespace ``release-tag`` value is refused
rather than read as "none".

**No side effects.** Pure stdlib, no network, no filesystem, no database,
no tenant policy, no credential handling: a rejection message is derived
from structure alone and never echoes a URL (a source URL may carry a
private-repo hint; the grammar refuses userinfo structurally).

Every error is a :class:`SourceRefError` (a ``ValueError``) carrying a
stable ``code``; hosts map it 1:1 onto their own typed refusal and keep
their product wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_REF_LENGTH",
    "MAX_SUBPATH_LENGTH",
    "MAX_URL_LENGTH",
    "ParsedRef",
    "SourceRefError",
    "parse_commit_sha",
    "parse_source_ref",
    "parse_subpath",
    "parse_url",
    "validate_archive_entry",
]

#: ``extension-sources.schema.json`` → ``properties.url.maxLength``.
MAX_URL_LENGTH = 512
#: ``extension-sources.schema.json`` → ``$defs.refStrategy.value.maxLength``
#: (and ``$defs.pinRecord.ref.maxLength``).
MAX_REF_LENGTH = 200
#: ``extension-sources.schema.json`` → ``$defs.subpath.maxLength``.
MAX_SUBPATH_LENGTH = 255
#: The archive entry cap (the pack contract caps item files at 200; the
#: headroom absorbs the manifest and directories before the typed cap).
MAX_ARCHIVE_ENTRIES = 512

#: The charter's closed strategy vocabulary (``sources.refStrategies``).
_REF_STRATEGIES = ("release-tag", "tag-pattern", "branch", "commit-pin")

#: ``extension-sources.schema.json`` → ``properties.url.pattern``, verbatim.
#: An ALLOWLIST: userinfo credentials (``@``), whitespace, backslashes,
#: query/fragment markers and non-https transports are structurally
#: inexpressible rather than filtered.
_URL_PATTERN = (
    r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]"
    r"(:[0-9]{1,5})?(/[A-Za-z0-9._~!$&'()*+,;=:%/-]*)?$"
)
_URL_RE = re.compile(_URL_PATTERN)
_URL_MIN_LENGTH = 12

#: Transports refused at the seam with a ``NET_DENIED`` code (the https-only
#: distribution surface, D13). ``http://`` is not a transport refusal but a
#: grammar miss (``URI_INVALID``), matching the schema's one accepted scheme.
_DENIED_SCHEMES = ("file://", "ftp://", "ssh://", "git://")

#: ``extension-sources.schema.json`` → ``$defs.refStrategy.value.pattern``.
_REF_PATTERN = r"^[A-Za-z0-9._/+*-]{1,200}$"
_REF_RE = re.compile(_REF_PATTERN)

#: ``extension-sources.schema.json`` → ``refStrategy.allOf[0].then.value``
#: (the commit-pin branch), verbatim. Lowercase only: git shas are emitted
#: lowercase, and accepting uppercase would make two spellings of one pin
#: compare unequal.
_COMMIT_SHA_PATTERN = r"^([0-9a-f]{40}|[0-9a-f]{64})$"
_COMMIT_SHA_RE = re.compile(_COMMIT_SHA_PATTERN)

#: ``extension-sources.schema.json`` → ``$defs.subpath.pattern``.
_SUBPATH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
_SUBPATH_RE = re.compile(_SUBPATH_PATTERN)

_ARCHIVE_MANIFEST = "manifest.json"
_ARCHIVE_ITEMS_PREFIX = "items/"
_ARCHIVE_ITEM_SUFFIX = ".json"


class SourceRefError(ValueError):
    """A typed source-grammar refusal (nothing was fetched or written).

    ``code`` is one of:

    * ``URI_INVALID`` — not a plain https URL (userinfo, query, fragment,
      whitespace, backslash, length, or a scheme other than https).
    * ``NET_DENIED`` — a transport this seam refuses outright
      (``file://`` / ``ftp://`` / ``ssh://`` / ``git://``).
    * ``REF_INVALID`` — an unknown strategy, a missing/forbidden value for
      a strategy, an unsafe generic ref (leading dash, whitespace, quotes,
      over-length) or a non-40/64-lowercase-hex commit pin.
    * ``SUBPATH_INVALID`` — traversal, absolute, backslash, trailing slash
      or over-length subpath.
    * ``ARCHIVE_PATH_TRAVERSAL`` — an archive entry name that is absolute,
      carries a backslash or contains a ``..`` segment.
    * ``ARCHIVE_ENTRY_FORBIDDEN`` — an archive entry outside the allowlist
      (``manifest.json`` and ``items/**.json``).
    * ``ARCHIVE_TOO_LARGE`` — a caller-enforced cap was exceeded (the
      module exposes :data:`MAX_ARCHIVE_ENTRIES`; the reader enforces it).

    The message is derived from structure only — it never echoes the
    rejected value (a URL can hint at a private repository; a ref can be
    attacker-controlled input).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ParsedRef:
    """A parsed ``refStrategy`` pair.

    ``value`` is the resolved ref *value* the descriptor carries (``None``
    for ``release-tag``, which names no value — resolution is host-side).
    ``strategy`` echoes the declared strategy, or ``None`` when the caller
    passed a bare ref with no strategy. ``commit_sha`` is set only when the
    value IS a commit pin, so a host can choose the sha-fetch path without
    re-deriving the grammar.
    """

    value: str | None
    strategy: str | None
    commit_sha: str | None

    @property
    def is_commit_pin(self) -> bool:
        """True when this pair pins an exact commit (strategy or detected)."""
        return self.commit_sha is not None


def parse_commit_sha(value: object) -> str:
    """Validate a 40- or 64-character lowercase hex commit sha.

    Strict by design: no trimming, no uppercase, no abbreviated sha — a
    short sha is ambiguous over time and an uppercase one is a second
    spelling of the same pin.
    """
    if not isinstance(value, str) or _COMMIT_SHA_RE.fullmatch(value) is None:
        raise SourceRefError(
            "REF_INVALID",
            "a commit pin must be 40 or 64 lowercase hex characters",
        )
    return value


def parse_url(url: object) -> str:
    """Validate the https-only source URL grammar (structural, pre-DNS).

    The exact ``extension-sources.schema.json`` allowlist pattern, plus the
    schema's length bounds. ``file://``/``ftp://``/``ssh://``/``git://`` are
    a typed ``NET_DENIED`` transport refusal; everything else that is not a
    plain https URL (``http://``, userinfo, query, fragment, backslash,
    whitespace, over/under-length) is ``URI_INVALID``. SSRF policy
    (loopback/private ranges, resolution pinning) belongs to the host's
    fetch guard, not here.
    """
    if not isinstance(url, str):
        raise SourceRefError("URI_INVALID", "a source url must be a string")
    if not url.strip():
        raise SourceRefError("URI_INVALID", "a source url is required")
    if url.lower().startswith(_DENIED_SCHEMES):
        raise SourceRefError(
            "NET_DENIED", "the url scheme is refused — sources are https-only"
        )
    if not _URL_MIN_LENGTH <= len(url) <= MAX_URL_LENGTH:
        raise SourceRefError(
            "URI_INVALID",
            f"a source url must be {_URL_MIN_LENGTH}-{MAX_URL_LENGTH} characters",
        )
    if "\\" in url or any(ch.isspace() for ch in url):
        raise SourceRefError("URI_INVALID", "the url carries a forbidden character")
    if _URL_RE.fullmatch(url) is None:
        raise SourceRefError(
            "URI_INVALID",
            "the url must be a plain https URL (no userinfo, no query, no fragment)",
        )
    return url


def _bounded_ref(value: object) -> str:
    """Validate the bounded generic ref grammar (surrounding space trimmed)."""
    if not isinstance(value, str):
        raise SourceRefError("REF_INVALID", "a ref must be a string")
    ref = value.strip()
    if not ref:
        raise SourceRefError("REF_INVALID", "a ref value is required")
    if len(ref) > MAX_REF_LENGTH:
        raise SourceRefError("REF_INVALID", f"a ref must be at most {MAX_REF_LENGTH} characters")
    if ref.startswith("-"):
        raise SourceRefError(
            "REF_INVALID", "a ref must not start with '-' (argv option injection)"
        )
    if _REF_RE.fullmatch(ref) is None:
        raise SourceRefError(
            "REF_INVALID", "a ref must not carry whitespace, quotes or exotic characters"
        )
    return ref


def parse_source_ref(
    ref: str | None, *, strategy: str | None = None
) -> ParsedRef:
    """Parse a source ref (bare value, or a ``refStrategy`` pair).

    ``strategy`` must be ``None`` or one of the charter's four
    (``release-tag``, ``tag-pattern``, ``branch``, ``commit-pin``).
    ``release-tag`` requires **no** value (the host resolves "latest
    release"); ``branch``/``tag-pattern`` require a bounded non-empty
    value; ``commit-pin`` requires a 40/64-lowercase-hex sha and fills
    ``commit_sha``. With ``strategy=None`` the ref is a bounded generic
    value, and a 40/64-lowercase-hex value is *detected* as a commit pin
    (``commit_sha`` set, ``strategy`` left ``None``). ``None``/blank yields
    the empty pin (the host clones the remote's default branch).

    Surrounding whitespace is trimmed (a ref is not a literal); interior
    whitespace, quotes and a leading dash are refused — a ref reaches a
    ``git`` argv list, so an option injection must be structurally
    impossible.
    """
    if strategy is not None and strategy not in _REF_STRATEGIES:
        raise SourceRefError(
            "REF_INVALID",
            "the strategy must be one of " + ", ".join(_REF_STRATEGIES),
        )
    if strategy == "release-tag":
        if ref is not None and not isinstance(ref, str):
            raise SourceRefError("REF_INVALID", "a ref must be a string")
        if ref not in (None, ""):
            raise SourceRefError(
                "REF_INVALID",
                "release-tag resolves the latest release host-side and takes no value",
            )
        return ParsedRef(None, "release-tag", None)
    if strategy == "commit-pin":
        sha = parse_commit_sha(ref if isinstance(ref, str) else "")
        return ParsedRef(sha, "commit-pin", sha)
    if strategy in ("branch", "tag-pattern"):
        value = _bounded_ref(ref)
        return ParsedRef(value, strategy, None)
    if ref is None:
        return ParsedRef(None, None, None)
    if not isinstance(ref, str):
        raise SourceRefError("REF_INVALID", "a ref must be a string")
    if not ref.strip():
        return ParsedRef(None, None, None)
    value = _bounded_ref(ref)
    if _COMMIT_SHA_RE.fullmatch(value) is not None:
        return ParsedRef(value, None, value)
    return ParsedRef(value, None, None)


def parse_subpath(subpath: str | None) -> str | None:
    """Validate the ``subpath`` grammar (the directory holding the manifests).

    No leading ``/``, no ``.``/``..`` segment, no backslash, no trailing
    slash, at most :data:`MAX_SUBPATH_LENGTH` characters. ``None``/blank
    means "the checkout root" and returns ``None``. Surrounding whitespace
    is trimmed; the grammar itself admits no whitespace. Directory
    existence and confinement inside the checkout are the host's job.
    """
    if subpath is None:
        return None
    if not isinstance(subpath, str):
        raise SourceRefError("SUBPATH_INVALID", "a subpath must be a string")
    if not subpath.strip():
        return None
    path = subpath.strip()
    if len(path) > MAX_SUBPATH_LENGTH:
        raise SourceRefError(
            "SUBPATH_INVALID", f"a subpath must be at most {MAX_SUBPATH_LENGTH} characters"
        )
    if path.startswith("/"):
        raise SourceRefError("SUBPATH_INVALID", "a subpath must not start with '/'")
    if "\\" in path:
        raise SourceRefError("SUBPATH_INVALID", "a subpath must not carry a backslash")
    if path.endswith("/"):
        raise SourceRefError("SUBPATH_INVALID", "a subpath must not end with '/'")
    if ".." in path.split("/"):
        raise SourceRefError("SUBPATH_INVALID", "a subpath must not contain a '..' segment")
    if _SUBPATH_RE.fullmatch(path) is None:
        raise SourceRefError(
            "SUBPATH_INVALID",
            "a subpath must be '/'-separated segments starting alphanumeric",
        )
    return path


def validate_archive_entry(name: str) -> str | None:
    """Validate one pack-archive entry name against the family allowlist.

    Traversal is refused first, whatever the entry kind: absolute names, a
    backslash anywhere, and any ``..`` segment are
    ``ARCHIVE_PATH_TRAVERSAL``. Directory entries (a trailing ``/``) are
    then allowed and returned as-is — the host skips them while
    materializing files (the zip writer records directories, and refusing
    them would break ordinary archives). Everything else must be exactly
    ``manifest.json`` or an ``items/**.json`` item file, else
    ``ARCHIVE_ENTRY_FORBIDDEN``.

    Reading the entry, bounding its size and writing it into a quarantine
    directory stay app-side; this function never touches the archive.
    """
    if not isinstance(name, str) or not name:
        raise SourceRefError("ARCHIVE_ENTRY_FORBIDDEN", "an archive entry needs a name")
    if name.startswith("/") or "\\" in name:
        raise SourceRefError(
            "ARCHIVE_PATH_TRAVERSAL",
            "an archive entry must not be absolute or carry a backslash",
        )
    if ".." in name.split("/"):
        raise SourceRefError(
            "ARCHIVE_PATH_TRAVERSAL", "an archive entry must not contain a '..' segment"
        )
    if name.endswith("/"):
        return name
    if name == _ARCHIVE_MANIFEST:
        return name
    if name.startswith(_ARCHIVE_ITEMS_PREFIX) and name.endswith(_ARCHIVE_ITEM_SUFFIX):
        return name
    raise SourceRefError(
        "ARCHIVE_ENTRY_FORBIDDEN",
        "an archive entry must be manifest.json or items/**.json",
    )

"""URL custody + remote-calendar identity for calendar bindings (C4 / H4).

Two laws from the family contract live here:

**A URL with a query string is a secret** (ADR-0052 §9 / Rev 3 C4). A
private ICS feed's bearer token rides in its query string, so the full
address lives only in the binding's sealed slot; the row (``options``)
keeps scheme+host+path — the same sanitization the error-scrub uses, but
at rest. :func:`split_url_custody` returns the sanitized address plus the
query part (``None`` when the URL never had one — no custody needed).

**One writer per remote calendar** (ADR-0052 §9 / Rev 3 H4).
:func:`derive_remote_calendar_id` builds the credential-free,
protocol-keyed identity of the remote calendar — the calendar id for
Google/Graph, the sanitized collection/feed URL for CalDAV/ICS — which the
binding row pins and the partial unique index enforces.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

#: The option keys carrying remote addresses.
URL_KEYS = ("url", "calendar_url", "base_url")

#: Identity for the calendar-id-keyed protocols (Google/Graph).
CALENDAR_ID_PROTOCOLS = ("google-calendar", "graph-calendar")


def sanitize_address(url: str) -> str:
    """``scheme://host[:port]/path`` — no query, no fragment, no userinfo."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "", "", ""))


def split_url_custody(url: str) -> tuple[str, str | None]:
    """Split an address into (sanitized, query) — query is ``None`` when the
    URL carried none, so callers only enter custody when a secret exists."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url, None
    if not parts.query:
        return url, None
    return sanitize_address(url), parts.query


def custody_check(options: dict[str, object]) -> dict[str, str]:
    """Return ``{key: full_url}`` for every address option whose query
    string makes it a secret. Empty when nothing needs custody."""
    carrying: dict[str, str] = {}
    for key in URL_KEYS:
        value = options.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        _, query = split_url_custody(value)
        if query:
            carrying[key] = value
    return carrying


def normalize_webcal(url: str) -> str:
    """``webcal://`` is an ``https://`` subscription in disguise."""
    stripped = url.strip()
    if stripped.lower().startswith("webcal://"):
        return "https://" + stripped[len("webcal://"):]
    return stripped


def derive_remote_calendar_id(protocol: str, options: dict[str, object]) -> str | None:
    """The credential-free remote-calendar identity for the writer lock.

    Google/Graph: the calendar id. CalDAV: the sanitized collection URL.
    ICS feeds are read-only and never bound as writers (``None``).
    """
    if protocol in CALENDAR_ID_PROTOCOLS:
        calendar_id = str(options.get("calendar_id") or "").strip()
        return calendar_id or None
    if protocol == "caldav":
        value = options.get("calendar_url")
        if isinstance(value, str) and value.strip():
            return sanitize_address(value) or None
    return None


__all__ = [
    "URL_KEYS",
    "custody_check",
    "derive_remote_calendar_id",
    "normalize_webcal",
    "sanitize_address",
    "split_url_custody",
]

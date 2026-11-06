"""Error-detail scrubbing at the host boundary (ADR-0052 §9, plan 52 §10 E12).

The vendored contract's transports build ``CalendarError("unreachable",
str(exc))`` — and an httpx exception message can embed the **full request
URL**. For a private ICS feed that URL carries a bearer token in its query
string (C4), so any provider error that reaches ``last_error``, a sync-log
audit row, a log line or an API response must be scrubbed here first: every
URL is reduced to ``scheme://host[:port]/path`` — query string, fragment and
userinfo (``user:pass@``) are stripped; a URL we cannot re-parse is dropped
entirely.

This module is the **one** choke point: the sync pipeline scrubs before
storing/surfacing (``sync._cap_error_detail``), and the API scrubs again on
the way out (defense in depth — a stored value never re-leaks). Log calls
that interpolate provider errors scrub too.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

#: Any URL-looking token (http, https, webcal, …). The negative character
#: class stops at whitespace and common delimiters so a URL inside a
#: sentence is scrubbed without eating the sentence.
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")

#: Replaces a URL whose pieces cannot be re-parsed safely.
_REDACTED_URL = "<url-redacted>"


def sanitize_url(url: str) -> str:
    """Reduce a URL to ``scheme://host[:port]/path`` — no query, no fragment,
    no userinfo. Unparseable or host-less URLs collapse to :data:`_REDACTED_URL`.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return _REDACTED_URL
    host = parts.hostname or ""
    if not host:
        return _REDACTED_URL
    # hostname strips userinfo already; re-attach an explicit port when set.
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "", "", ""))


def sanitize_error_detail(detail: str | None) -> str | None:
    """Scrub every URL in an error string (query strings carry tokens).

    Applied to anything a provider error puts into ``last_error``, a
    ``calendar_sync_logs.error_detail`` audit row, a log line or an API
    response (ADR-0052 §9: no credential material in any of them).
    """
    if not detail:
        return detail
    return _URL_RE.sub(lambda match: sanitize_url(match.group(0)), detail)


__all__ = ["sanitize_error_detail", "sanitize_url"]

"""FHIR R4 Bundle construction for search results.

FHIR search endpoints return ``Bundle`` resources with ``type=searchset``, a
``total`` integer, and a ``link`` array for pagination. This module builds
those Bundles from a list of FHIR-canonical resource dicts.

Pagination uses RFC 6570-style templates with offset-based cursors. The
``link[]`` includes ``self`` (current page), ``first``, ``previous``, ``next``,
and ``last`` when applicable.
"""

import math
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode


def build_search_bundle(
    base_url: str,
    path: str,
    query_string: bytes,
    resources: List[Dict[str, Any]],
    total: int,
    offset: int,
    count: int,
    *,
    include_resources: Optional[List[Dict[str, Any]]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a FHIR Bundle for a search response.

    Args:
        base_url: scheme + host (no trailing slash), e.g. ``https://host/api/v1/fhir/R4``.
        path: resource path, e.g. ``/Observation``.
        query_string: raw request query string (bytes) for self-link preservation.
        resources: list of FHIR-canonical resource dicts (current page).
        total: total match count (independent of pagination).
        offset: 0-based offset of the first entry in this page.
        count: page size (the ``_count`` param value).
        include_resources: optional additional resources fetched via ``_include``
            (appended after the primary matches; not counted in ``total``).
        meta: optional Bundle-level meta block.

    Returns:
        A FHIR R4 Bundle dict with ``type=searchset``, ``total``, ``entry[]``,
        and ``link[]`` for self/first/previous/next/last.
    """
    # Parse the raw query string so we can mutate it for pagination links.
    raw_qs = query_string.decode("utf-8") if isinstance(query_string, bytes) else query_string
    parsed = _parse_qs(raw_qs)

    # Compute total pages.
    total_pages = max(1, math.ceil(total / count)) if count > 0 else 1
    current_page = (offset // count) + 1 if count > 0 else 1

    # Build the entry list.
    entries: List[Dict[str, Any]] = []
    for r in resources:
        rt = r.get("resourceType")
        rid = r.get("id")
        full_url = f"{base_url}{path}/{rid}" if rid else f"{base_url}{path}/_search"
        entries.append({"fullUrl": full_url, "resource": r})
    if include_resources:
        for r in include_resources:
            rid = r.get("id")
            rt = r.get("resourceType")
            full_url = f"{base_url}/{rt}/{rid}" if rid and rt else None
            entry: Dict[str, Any] = {"resource": r}
            if full_url:
                entry["fullUrl"] = full_url
            entries.append(entry)

    # Build pagination links.
    links: List[Dict[str, Any]] = []
    links.append({"relation": "self", "url": _build_url(base_url, path, parsed)})
    links.append({"relation": "first", "url": _build_url(base_url, path, _with_page(parsed, 0, count))})
    links.append({"relation": "last", "url": _build_url(base_url, path, _with_page(parsed, (total_pages - 1) * count, count))})
    if current_page > 1:
        links.append({"relation": "previous", "url": _build_url(base_url, path, _with_page(parsed, max(0, offset - count), count))})
    if current_page < total_pages:
        links.append({"relation": "next", "url": _build_url(base_url, path, _with_page(parsed, offset + count, count))})

    bundle: Dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": links,
        "entry": entries,
    }
    if meta is not None:
        bundle["meta"] = meta
    else:
        from app.services.fhir_helpers import build_meta
        bundle["meta"] = build_meta(provenance=False)
    return bundle


def _parse_qs(raw: str) -> List[tuple]:
    """Parse a query string into a list of (key, value) pairs, preserving order
    and repeated keys. Empty input returns []."""
    if not raw:
        return []
    pairs: List[tuple] = []
    for chunk in raw.split("&"):
        if not chunk:
            continue
        if "=" in chunk:
            k, v = chunk.split("=", 1)
        else:
            k, v = chunk, ""
        pairs.append((k, v))
    return pairs


def _build_url(base_url: str, path: str, pairs: List[tuple]) -> str:
    if not pairs:
        return f"{base_url}{path}"
    qs = urlencode(pairs)
    return f"{base_url}{path}?{qs}"


def _with_page(pairs: List[tuple], offset: int, count: int) -> List[tuple]:
    """Replace any existing ``_offset``/``page`` param with the given offset.

    We emit ``_offset`` (non-standard but unambiguous) and remove any ``page``
    param to avoid conflicting pagination instructions.
    """
    filtered = [(k, v) for k, v in pairs if k not in ("_offset", "page")]
    filtered.append(("_offset", str(offset)))
    return filtered

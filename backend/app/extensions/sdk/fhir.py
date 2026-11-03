"""FHIR R4 client helpers — Bundle pagination, OperationOutcome
diagnostics and the create/conditional-update verbs, on top of the
polite transport runtime (:mod:`app.extensions.runtime.http`).

Reused by the FHIR Server extension (``app/extensions/builtin/
fhir_server``) and the FHIR facade. Pure FHIR — no SMART/auth coupling:
``fhir_search`` takes an ``httpx.AsyncClient`` plus an optional
``access_token`` (tokenless for local/test servers; the caller owns token
lifecycle for SMART). Errors are the charter typed model
(:class:`app.extensions.errors.ExtensionError`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from app.extensions.errors import ExtensionError
from app.extensions.runtime.http import guarded_request, http_request
from app.schemas.fhir.observation import ObservationCreate
from app.services.fhir_helpers import _as_list, _flatten_interpretation

logger = logging.getLogger(__name__)

# Default upper bound on Bundle pagination. ``paginate_bundle`` and
# ``fhir_search`` accept ``max_pages=None`` to opt out (truly unbounded),
# but the default is capped so an extension that forgets the argument
# can't walk a multi-million-entry Bundle forever.
DEFAULT_MAX_PAGES = 100


async def paginate_bundle(
    http_client: httpx.AsyncClient,
    url: str,
    *,
    access_token: str | None,
    params: dict[str, Any] | None = None,
    max_pages: int | None = DEFAULT_MAX_PAGES,
    max_retries: int = 3,
) -> AsyncIterator[dict[str, Any]]:
    """Yield each ``entry[].resource`` from a FHIR searchset Bundle.

    Follows ``link[rel=next].url`` (FHIR servers return absolute URLs —
    every hop rides the guarded transport) until exhausted or
    ``max_pages`` is reached. ``params`` apply only to the first request
    (subsequent pages use the server-provided ``next`` URL verbatim).
    """
    next_url: str | None = url
    next_params: dict[str, Any] | None = params
    pages = 0
    while next_url:
        if max_pages is not None and pages >= max_pages:
            logger.info("paginate_bundle: reached max_pages=%d, stopping.", max_pages)
            return
        pages += 1
        bundle = await http_request(
            http_client,
            "GET",
            next_url,
            access_token=access_token,
            params=next_params,
            max_retries=max_retries,
        )
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            raise ExtensionError(
                "protocol",
                f"expected a FHIR Bundle from {next_url}; got {type(bundle).__name__}.",
            )
        for entry in bundle.get("entry") or []:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict):
                yield resource
        next_url = _next_link(bundle)
        next_params = None  # the next URL already carries the pagination offset


def _next_link(bundle: dict[str, Any]) -> str | None:
    for link in bundle.get("link") or []:
        if isinstance(link, dict) and link.get("relation") == "next":
            url = link.get("url")
            if isinstance(url, str):
                return url
    return None


def response_detail(response: httpx.Response, default: str = "no response body") -> str:
    """Human-readable detail from an FHIR error response (H8):
    ``OperationOutcome.issue[].diagnostics`` first, then the capped raw
    body."""
    try:
        return parse_operation_outcome(response.json())
    except (ValueError, TypeError):
        return response.text[:300] if response.content else default


async def fhir_search(
    http_client: httpx.AsyncClient,
    base_url: str,
    resource_type: str,
    params: dict[str, Any],
    *,
    access_token: str | None = None,
    max_pages: int | None = DEFAULT_MAX_PAGES,
) -> list[dict[str, Any]]:
    """FHIR search returning a flat list of resource dicts.

    Pure FHIR — no auth coupling. ``access_token`` (optional) is sent as a Bearer
    header; pass ``None`` for tokenless servers (e.g. a local HAPI FHIR). Token
    lifecycle (acquire / refresh-on-401) belongs to the caller (the provider's
    ``_authorized_search`` for SMART; nothing for tokenless). ``params`` apply
    only to the first request; Bundle pagination follows ``link[rel=next]``.

    The default ``max_pages`` is :data:`DEFAULT_MAX_PAGES` (100) — pass
    ``max_pages=None`` explicitly for truly unbounded iteration.
    """
    base = base_url.rstrip("/")
    url = f"{base}/{resource_type}"
    return [
        r
        async for r in paginate_bundle(
            http_client, url, access_token=access_token, params=params, max_pages=max_pages
        )
    ]


def parse_operation_outcome(obj: Any) -> str:
    """Best-effort human-readable error from a FHIR OperationOutcome dict/response.

    Returns the first issue's ``diagnostics``/``details.text``/``code``/``severity``,
    or a generic message if the shape isn't recognized.

    Deliberately does **not** stringify a non-OperationOutcome dict (e.g. a
    returned ``Patient``/``Observation``) — that would dump PHI into an error
    string. Only the ``OperationOutcome`` arm (or a bare ``text.div``) is
    surfaced; everything else gets a generic placeholder.
    """
    if isinstance(obj, dict):
        if obj.get("resourceType") == "OperationOutcome" or "issue" in obj:
            issues = obj.get("issue") or []
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                msg = (
                    issue.get("diagnostics")
                    or (issue.get("details") or {}).get("text")
                    or issue.get("code")
                )
                if msg:
                    sev = issue.get("severity")
                    return f"{sev}: {msg}" if sev else str(msg)
            return "OperationOutcome with no parseable issue."
        # A non-OperationOutcome dict (e.g. a returned resource) — don't dump
        # its body (may carry PHI). Surface the resourceType if present so
        # the error is still diagnosable.
        rt = obj.get("resourceType")
        return f"unexpected FHIR resource {rt}" if rt else "unrecognized response"
    if obj is None:
        return "no response body"
    return str(obj)[:500]


def _parse_fhir_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Replace only a TRAILING 'Z' (UTC designator). Replacing every 'Z'
        # would corrupt a datetime whose value happened to contain one.
        cleaned = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def fhir_observation_to_create(
    fhir_obs: dict[str, Any],
    *,
    tenant_id: UUID,
    patient_id: UUID,
) -> ObservationCreate | None:
    """Convert a FHIR R4 Observation dict to an :class:`ObservationCreate`.

    Attached to the **local** ``patient_id`` (the remote patient id is only used
    in the FHIR search; results are normalized to the local patient). Returns
    ``None`` for observations that can't be mapped (no code, or no usable value).

    The biomarker engine (:func:`map_observations_to_biomarkers`) resolves
    ``biomarker_id`` afterwards from ``code.coding[]`` (LOINC) / ``code.text``.
    """
    code = fhir_obs.get("code")
    if not isinstance(code, dict):
        return None  # can't be mapped to a biomarker

    value_quantity = fhir_obs.get("valueQuantity")
    value_string = fhir_obs.get("valueString")
    component = fhir_obs.get("component")
    # H2: multi-component observations (blood pressure, panels) have no
    # valueQuantity/valueString — don't drop them.
    if not value_quantity and not value_string and not component:
        return None  # no usable value

    effective = _parse_fhir_datetime(fhir_obs.get("effectiveDateTime")) or _parse_fhir_datetime(
        (fhir_obs.get("effectivePeriod") or {}).get("start")
    )
    status = (fhir_obs.get("status") or "final").lower()

    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "status": status,
        "code": code,
        "subject": {"reference": f"Patient/{patient_id}"},
        "effective_datetime": effective,
        "interpretation": _flatten_interpretation(fhir_obs.get("interpretation")),
        "performer": [
            {"reference": p.get("reference"), "display": p.get("display")}
            for p in (fhir_obs.get("performer") or [])
            if isinstance(p, dict) and (p.get("reference") or p.get("display"))
        ]
        or None,
        # FHIR Observation.category is 0..* (a list of CodeableConcept). Keep
        # the canonical list shape from the source FHIR server so data stays
        # FHIR-compatible through pull -> store -> push round-trips.
        "category": _as_list(fhir_obs.get("category")),
        # H2: preserve component[] (BP, panels) and note[] (→ comment).
        "component": _as_list(component),
    }
    # FHIR note[0].text → the ORM ``comment`` column (single string).
    _note_list = _as_list(fhir_obs.get("note"))
    if _note_list and isinstance(_note_list[0], dict) and _note_list[0].get("text"):
        kwargs["comment"] = _note_list[0]["text"]
    if isinstance(value_quantity, dict):
        kwargs["value_quantity"] = value_quantity
    if isinstance(value_string, str):
        kwargs["value_string"] = value_string

    # H6: preserve the full referenceRange[] list (canonical FHIR shape) —
    # multi-range observations (age-stratified) no longer lose data.
    reference_range = _extract_reference_range(fhir_obs.get("referenceRange"))
    if reference_range is not None:
        kwargs["reference_range"] = reference_range

    try:
        return ObservationCreate(**kwargs)
    except Exception as e:
        # Surface the offending observation's id + code so a silent drop is
        # triageable (the engine counts dropped observation downstream). Log
        # the FHIR id + first coding code only — no PHI/value in the message.
        obs_id = fhir_obs.get("id")
        first_code = ""
        codings = (code.get("coding") or []) if isinstance(code, dict) else []
        if codings and isinstance(codings[0], dict):
            first_code = codings[0].get("code") or ""
        logger.warning(
            "Skipping FHIR observation id=%s code=%s (failed to build ObservationCreate): %s",
            obs_id,
            first_code,
            e,
        )
        return None


def _extract_reference_range(raw: Any) -> list[dict[str, Any]] | None:
    """FHIR referenceRange[] → the canonical list (passthrough).

    H6: previously this flattened only ``referenceRange[0]`` to ``{min, max}``,
    losing multi-range observations (age-stratified) and dropping ``type``,
    ``appliesTo``, ``text``, and unit info from each range. Now returns the
    full canonical FHIR list so the round-trip preserves all data.
    """
    if not isinstance(raw, list) or not raw:
        return None
    return raw


async def fhir_create(
    http_client: httpx.AsyncClient,
    base_url: str,
    resource_type: str,
    body: dict[str, Any],
    *,
    access_token: str | None = None,
    max_retries: int = 3,
) -> tuple[int, dict[str, Any] | None]:
    """FHIR create: ``POST /{Resource}`` with a body.

    Used by H3 (remote Provenance POST after a push). Same retry/error
    contract as :func:`fhir_conditional_update` (the polite transport
    runtime: full-jitter backoff on 429/5xx/network errors, immediate
    raise on 401/403). Returns ``(status_code, response_dict_or_None)``.
    """
    base = base_url.rstrip("/")
    url = f"{base}/{resource_type}"
    headers: dict[str, str] = {
        "Accept": "application/fhir+json, application/json",
        "Content-Type": "application/fhir+json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    response = await guarded_request(
        http_client, "POST", url, headers=headers, json_body=body, max_retries=max_retries
    )
    status = response.status_code
    if status >= 400:
        # 401/403 already raised by the runtime; the only remaining 4xx
        # are non-retryable client errors (400/404/409/422/...).
        raise ExtensionError(
            "protocol", f"POST {url} -> {status}: {_enrich_error(response)}", status=status
        )
    return status, _safe_json(response)


async def fhir_conditional_update(
    http_client: httpx.AsyncClient,
    base_url: str,
    resource_type: str,
    body: dict[str, Any],
    *,
    search_params: dict[str, str],
    access_token: str | None = None,
    if_none_match: str | None = None,
    if_match: str | None = None,
    if_none_exist: str | None = None,
    max_retries: int = 3,
) -> tuple[int, dict[str, Any] | None]:
    """FHIR conditional update: ``PUT /{Resource}?{search_params}`` with a body.

    Reused by the Stage 2b push (``integrations/fhir_server``) and, later, the
    Stage 3 facade. Pure FHIR — no auth coupling: ``access_token`` (optional) is
    sent as a Bearer header; pass ``None`` for tokenless servers. Token
    lifecycle (acquire / refresh-on-401) belongs to the caller.

    Retry / error semantics ride the polite transport runtime
    (:func:`app.extensions.runtime.http.guarded_request` — full-jitter
    backoff on 429/5xx/network errors; immediate raise on 401/403).

    Conditional-update headers (FHIR R4 §2.1.1.5):

    - ``if_match`` (``If-Match``) — version-aware update (``W/"123"`` or ``ETag``).
    - ``if_none_match`` (``If-None-Match``) — ``*`` to update unconditionally
      only if no version exists at the id, or an ETag for optimistic locking.
    - ``if_none_exist`` (``If-None-Exist``) — a search query string *applied
      to the PUT target*. The server creates the resource **only if no
      resource matches** that search; otherwise it updates the matching one.
      This is the common EHR "create-if-not-already-there" upsert idiom
      (e.g. ``If-None-Exist: identifier=urn:ha|{uuid}``).

    Returns ``(status_code, response_dict_or_None)`` so the caller can apply its
    own policy:

    - ``200`` / ``201`` → updated / created; response is the persisted resource.
    - ``412`` → precondition not met (``If-Match`` / ``If-None-Match``); treat
      as "skipped" (no change needed). Response body (often an
      ``OperationOutcome``) is returned for inspection — NOT raised.

    Auth failures (401/403) raise ``ExtensionError("auth")``,
    rate-limiting (429 after retries) raises ``ExtensionError("rate-limited",
    retry_after_ms=...)``, other 4xx raise ``protocol``, 5xx exhaustion
    ``provider`` and network failure ``unreachable`` — the charter model.
    """
    base = base_url.rstrip("/")
    url = f"{base}/{resource_type}"
    headers: dict[str, str] = {
        "Accept": "application/fhir+json, application/json",
        "Content-Type": "application/fhir+json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if if_none_match is not None:
        headers["If-None-Match"] = if_none_match
    if if_match is not None:
        headers["If-Match"] = if_match
    if if_none_exist is not None:
        # FHIR R4 §2.1.1.5: a search query string applied to the PUT target —
        # "create only if none matches"; otherwise update the match.
        headers["If-None-Exist"] = if_none_exist

    response = await guarded_request(
        http_client,
        "PUT",
        url,
        headers=headers,
        params=search_params,
        json_body=body,
        max_retries=max_retries,
    )
    status = response.status_code
    # 412 is the expected "precondition not met" outcome — return, don't raise.
    if status == 412:
        return 412, _safe_json(response)
    if status >= 400:
        # 401/403 already raised by the runtime; remaining 4xx are
        # non-retryable client errors.
        raise ExtensionError(
            "protocol", f"PUT {url} -> {status}: {_enrich_error(response)}", status=status
        )
    # 200/201 (and any other 2xx) — success.
    return status, _safe_json(response)


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    """Parse a JSON object body, returning ``None`` for empty/non-JSON bodies."""
    if not response.content:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _enrich_error(response: httpx.Response, default: str = "no response body") -> str:
    """Extract a human-readable error detail from a FHIR error response.

    H8: tries ``parse_operation_outcome`` (which understands FHIR
    OperationOutcome ``issue[].diagnostics``) before falling back to the raw
    response body. This surfaces actionable server messages (e.g. "code system
    http://loinc.org not recognized") instead of truncated HTML/JSON.
    """
    try:
        return parse_operation_outcome(response.json())
    except (ValueError, TypeError):
        return response.text[:300] if response.content else default

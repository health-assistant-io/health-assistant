"""FHIR R4 client helpers built on top of :mod:`integrations.sdk.http`.

Reused by the Stage 2 FHIR client provider (``integrations/fhir_server``) and,
later, by the Stage 3 FHIR facade. Pure FHIR — no SMART/auth coupling:
``fhir_search`` takes an ``httpx.AsyncClient`` plus an optional ``access_token``
(tokenless for local/test servers; the caller owns token lifecycle for SMART).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import httpx

from app.schemas.fhir.observation import ObservationCreate
from app.services.fhir_helpers import _as_list, _flatten_interpretation
from integrations.sdk.exceptions import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationRateLimitError,
)
from integrations.sdk.http import paginate_bundle

logger = logging.getLogger(__name__)


async def fhir_search(
    http_client: httpx.AsyncClient,
    base_url: str,
    resource_type: str,
    params: Dict[str, Any],
    *,
    access_token: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """FHIR search returning a flat list of resource dicts.

    Pure FHIR — no auth coupling. ``access_token`` (optional) is sent as a Bearer
    header; pass ``None`` for tokenless servers (e.g. a local HAPI FHIR). Token
    lifecycle (acquire / refresh-on-401) belongs to the caller (the provider's
    ``_authorized_search`` for SMART; nothing for tokenless). ``params`` apply
    only to the first request; Bundle pagination follows ``link[rel=next]``.
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
        return str(obj.get("text", {}).get("div") or obj)[:500]
    return str(obj)[:500]


def _parse_fhir_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def fhir_observation_to_create(
    fhir_obs: Dict[str, Any],
    *,
    tenant_id: UUID,
    patient_id: UUID,
) -> Optional[ObservationCreate]:
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

    kwargs: Dict[str, Any] = {
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
        logger.warning("Skipping FHIR observation (failed to build ObservationCreate): %s", e)
        return None


def _extract_reference_range(raw: Any) -> Optional[List[Dict[str, Any]]]:
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
    body: Dict[str, Any],
    *,
    access_token: Optional[str] = None,
    max_retries: int = 3,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """FHIR create: ``POST /{Resource}`` with a body.

    Used by H3 (remote Provenance POST after a push). Same retry/error
    contract as :func:`fhir_conditional_update`. Returns
    ``(status_code, response_dict_or_None)``.
    """
    base = base_url.rstrip("/")
    url = f"{base}/{resource_type}"
    headers: Dict[str, str] = {
        "Accept": "application/fhir+json, application/json",
        "Content-Type": "application/fhir+json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    attempt = 0
    backoff = 1.0
    while True:
        try:
            response = await http_client.request(
                "POST", url, headers=headers, json=body
            )
        except (httpx.RequestError, httpx.TimeoutException) as e:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(f"Network error contacting {url}: {e}") from e
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        status = response.status_code
        if status in (401, 403):
            raise IntegrationAuthError(f"{url} returned {status}: {_enrich_error(response)}")
        if status == 429:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationRateLimitError(f"Rate limited by {url} after {max_retries} attempts.")
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            await asyncio.sleep(wait)
            backoff *= 2
            continue
        if status >= 500:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(f"POST {url} -> {status}: {_enrich_error(response)}")
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        if status >= 400:
            raise IntegrationDataError(f"POST {url} -> {status}: {_enrich_error(response)}")
        return status, _safe_json(response)


async def fhir_conditional_update(
    http_client: httpx.AsyncClient,
    base_url: str,
    resource_type: str,
    body: Dict[str, Any],
    *,
    search_params: Dict[str, str],
    access_token: Optional[str] = None,
    if_none_match: Optional[str] = None,
    if_match: Optional[str] = None,
    max_retries: int = 3,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """FHIR conditional update: ``PUT /{Resource}?{search_params}`` with a body.

    Reused by the Stage 2b push (``integrations/fhir_server``) and, later, the
    Stage 3 facade. Pure FHIR — no auth coupling: ``access_token`` (optional) is
    sent as a Bearer header; pass ``None`` for tokenless servers. Token
    lifecycle (acquire / refresh-on-401) belongs to the caller.

    Returns ``(status_code, response_dict_or_None)`` so the caller can apply its
    own policy:

    - ``200`` / ``201`` → updated / created; response is the persisted resource.
    - ``412`` → precondition not met (``If-Match`` / ``If-None-Match``); treat
      as "skipped" (no change needed). Response body (often an
      ``OperationOutcome``) is returned for inspection — NOT raised.

    Auth failures (401/403) raise :class:`IntegrationAuthError`, rate-limiting
    (429) raises :class:`IntegrationRateLimitError`, other 4xx and unrecoverable
    5xx/network errors raise :class:`IntegrationDataError` — same hierarchy as
    :func:`integrations.sdk.http.http_request`.
    """
    base = base_url.rstrip("/")
    url = f"{base}/{resource_type}"
    headers: Dict[str, str] = {
        "Accept": "application/fhir+json, application/json",
        "Content-Type": "application/fhir+json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if if_none_match is not None:
        headers["If-None-Match"] = if_none_match
    if if_match is not None:
        headers["If-Match"] = if_match

    attempt = 0
    backoff = 1.0
    while True:
        try:
            response = await http_client.request(
                "PUT", url, headers=headers, params=search_params, json=body
            )
        except (httpx.RequestError, httpx.TimeoutException) as e:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(f"Network error contacting {url}: {e}") from e
            logger.warning("Network error PUT %s (attempt %d/%d): %s", url, attempt, max_retries, e)
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        status = response.status_code
        if status in (401, 403):
            raise IntegrationAuthError(f"{url} returned {status}: {_enrich_error(response)}")
        if status == 429:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationRateLimitError(f"Rate limited by {url} after {max_retries} attempts.")
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            logger.warning("Rate limited by %s. Waiting %ss.", url, wait)
            await asyncio.sleep(wait)
            backoff *= 2
            continue
        if status >= 500:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(f"PUT {url} -> {status}: {_enrich_error(response)}")
            logger.warning("Server error PUT %s -> %d (attempt %d/%d)", url, status, attempt, max_retries)
            await asyncio.sleep(backoff)
            backoff *= 2
            continue
        # 412 is the expected "precondition not met" outcome — return, don't raise.
        if status == 412:
            return 412, _safe_json(response)
        if status >= 400:
            raise IntegrationDataError(f"PUT {url} -> {status}: {_enrich_error(response)}")
        # 200/201 (and any other 2xx) — success.
        return status, _safe_json(response)


def _safe_json(response: httpx.Response) -> Optional[Dict[str, Any]]:
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

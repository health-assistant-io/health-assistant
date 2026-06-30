"""FHIR R4 conformant facade router.

Mounted at ``/api/v1/fhir/R4``. This router exposes a FHIR R4 REST API on top
of the existing ORM models. It is the **interop surface only** — external
systems (FHIR servers, HL7 importers, export/import jobs, SMART-on-FHIR
clients). The frontend does not use this router; it speaks the domain endpoints
(``/patients/*``, ``/observations/*``, ``/examinations/*``, ...) which return
ORM-shape dicts optimized for the UI.

Audit items resolved by this router (Phase 5+6):
- C2: list endpoints return FHIR Bundles (type=searchset) with pagination links
- C3: standard search params (_id, _lastUpdated, _count, _sort, _format)
- C4: POST returns 201 + Location header + canonical FHIR JSON
- C5: DELETE soft-deletes; subsequent reads return 410 Gone (tombstone)
- C7-C16: resources registered in RESOURCE_REGISTRY are exposed
"""

from typing import Any, Dict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user_with_tenant_override as get_current_user
from app.facade import crud
from app.facade.registry import RESOURCE_REGISTRY, register_all
from app.facade.responses import (
    created_response,
    gone,
    invalid,
    no_content,
    not_found,
    ok_response,
    operation_outcome,
)
from app.services.fhir_facade_service import build_capability_statement
from app.services.fhir_helpers import FhirSerializationError
from app.schemas.user import TokenData


# Trigger resource registration at import time.
register_all()


router = APIRouter(prefix="/fhir/R4", tags=["fhir-r4"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _facade_base_url(request: Request) -> str:
    """Compute the absolute base URL of the facade from the incoming request."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip()
    scheme = forwarded_proto or request.url.scheme or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    return f"{scheme}://{host}/api/v1/fhir/R4"


def _require_entry(resource_type: str):
    """Return the ResourceEntry for the type or None. Caller handles the 404."""
    return RESOURCE_REGISTRY.get(resource_type)


def _query_params(request: Request):
    """Extract the raw multi-value query params as a list of (key, value).

    Starlette QueryParams is a multi-dict but doesn't expose ``multi()``
    directly; iterate via ``.multi_items()`` instead.
    """
    return [(k, v) for k, v in request.query_params.multi_items()]


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

@router.get("/metadata", response_model=None)
async def capability_statement(request: Request) -> Dict[str, Any]:
    """FHIR R4 CapabilityStatement (no auth — spec requirement).

    Cacheable for 5 minutes. Built dynamically from RESOURCE_REGISTRY.
    """
    cs = build_capability_statement(_facade_base_url(request))
    return JSONResponse(
        status_code=200,
        content=cs,
        headers={"Cache-Control": "public, max-age=300"},
    )


# ---------------------------------------------------------------------------
# Generic search (GET /{Resource})
# ---------------------------------------------------------------------------

@router.get("/{resource_type}", response_model=None)
async def search_resources(
    resource_type: str,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Search resources. Returns a FHIR Bundle (type=searchset)."""
    if resource_type == "metadata":
        # Defensive: metadata is handled by the explicit route above.
        return await capability_statement(request)

    entry = _require_entry(resource_type)
    if entry is None:
        return operation_outcome(
            "error",
            "not-found",
            f"Resource type {resource_type} not supported",
            404,
        )
    if "search-type" not in entry.interactions:
        return operation_outcome(
            "error",
            "not-supported",
            f"search-type not supported for {resource_type}",
            405,
        )

    try:
        bundle = await crud.search(
            entry=entry,
            query_params=_query_params(request),
            current_user=current_user,
            db=db,
            base_url=_facade_base_url(request),
        )
    except FhirSerializationError as e:
        return invalid(str(e))
    return JSONResponse(status_code=200, content=bundle)


# ---------------------------------------------------------------------------
# Generic read (GET /{Resource}/{id})
# ---------------------------------------------------------------------------

@router.get("/{resource_type}/{resource_id}", response_model=None)
async def read_resource(
    resource_type: str,
    resource_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Read a single resource by id."""
    entry = _require_entry(resource_type)
    if entry is None:
        return operation_outcome(
            "error",
            "not-found",
            f"Resource type {resource_type} not supported",
            404,
        )
    if "read" not in entry.interactions:
        return operation_outcome(
            "error",
            "not-supported",
            f"read not supported for {resource_type}",
            405,
        )

    result = await crud.read(entry, resource_id, current_user, db)
    if result is None:
        return not_found(resource_type, resource_id)
    if isinstance(result, dict) and result.get("_tombstone"):
        return gone(resource_type, resource_id)
    return ok_response(result, etag=f'W/"{result.get("meta", {}).get("versionId", "1")}"')


# ---------------------------------------------------------------------------
# Generic create (POST /{Resource})
# ---------------------------------------------------------------------------

@router.post("/{resource_type}", response_model=None, status_code=201)
async def create_resource(
    resource_type: str,
    payload: Dict[str, Any],
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Create a resource from canonical FHIR JSON. Returns 201 + Location."""
    entry = _require_entry(resource_type)
    if entry is None:
        return operation_outcome(
            "error",
            "not-found",
            f"Resource type {resource_type} not supported",
            404,
        )
    if "create" not in entry.interactions:
        return operation_outcome(
            "error",
            "not-supported",
            f"create not supported for {resource_type}",
            405,
        )

    try:
        fhir_response = await crud.create(entry, payload, current_user, db)
    except FhirSerializationError as e:
        return invalid(str(e))
    except PermissionError as e:
        return operation_outcome("error", "not-supported", str(e), 405)

    rid = fhir_response.get("id", "")
    location = f"{_facade_base_url(request)}/{resource_type}/{rid}"
    etag = f'W/"{fhir_response.get("meta", {}).get("versionId", "1")}"'
    last_modified = fhir_response.get("meta", {}).get("lastUpdated")
    return created_response(fhir_response, location=location, etag=etag, last_modified=last_modified)


# ---------------------------------------------------------------------------
# Generic update (PUT /{Resource}/{id})
# ---------------------------------------------------------------------------

@router.put("/{resource_type}/{resource_id}", response_model=None)
async def update_resource(
    resource_type: str,
    resource_id: str,
    payload: Dict[str, Any],
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update a resource by id (full replacement).

    F5: honors the ``If-Match`` header for optimistic locking. If the header
    is present (e.g. ``If-Match: W/"3"``), the version must match the current
    row's version or HTTP 412 Precondition Failed is returned.
    """
    entry = _require_entry(resource_type)
    if entry is None:
        return operation_outcome(
            "error",
            "not-found",
            f"Resource type {resource_type} not supported",
            404,
        )
    if "update" not in entry.interactions:
        return operation_outcome(
            "error",
            "not-supported",
            f"update not supported for {resource_type}",
            405,
        )

    try:
        result = await crud.update(
            entry,
            resource_id,
            payload,
            current_user,
            db,
            if_match=request.headers.get("if-match"),
        )
    except FhirSerializationError as e:
        return invalid(str(e))
    except PermissionError as e:
        return operation_outcome("error", "not-supported", str(e), 405)
    except crud.PreconditionFailed as e:
        # F5: If-Match version mismatch → 412 Precondition Failed (RFC 7232).
        return operation_outcome(
            "error",
            "conflict",
            f"Version conflict for {e.resource_type}/{e.resource_id}: "
            f"If-Match expected version {e.expected}, actual version {e.actual}.",
            412,
        )

    if result is None:
        return not_found(resource_type, resource_id)
    etag = f'W/"{result.get("meta", {}).get("versionId", "1")}"'
    return ok_response(result, etag=etag)


# ---------------------------------------------------------------------------
# Generic delete (DELETE /{Resource}/{id})
# ---------------------------------------------------------------------------

@router.delete("/{resource_type}/{resource_id}", response_model=None)
async def delete_resource(
    resource_type: str,
    resource_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Soft-delete a resource. Subsequent reads return 410 Gone."""
    entry = _require_entry(resource_type)
    if entry is None:
        return operation_outcome(
            "error",
            "not-found",
            f"Resource type {resource_type} not supported",
            404,
        )
    if "delete" not in entry.interactions:
        return operation_outcome(
            "error",
            "not-supported",
            f"delete not supported for {resource_type}",
            405,
        )

    try:
        success = await crud.delete(entry, resource_id, current_user, db)
    except PermissionError as e:
        return operation_outcome("error", "not-supported", str(e), 405)

    if not success:
        return not_found(resource_type, resource_id)
    return no_content()

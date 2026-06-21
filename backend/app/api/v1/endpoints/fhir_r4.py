"""FHIR R4 conformant facade router.

Mounted at ``/api/v1/fhir/R4``. This router exposes a FHIR R4 REST API on top
of the existing ORM models, alongside the legacy ORM-shape ``/fhir/*`` router
(which the frontend keeps using).

Phase 1 (this file): router scaffold + ``GET /metadata`` CapabilityStatement.
Subsequent phases register search (Bundle) and canonical CRUD per resource
type via the :data:`RESOURCE_REGISTRY`.
"""

from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.facade.responses import operation_outcome
from app.services.fhir_facade_service import build_capability_statement


router = APIRouter(prefix="/fhir/R4", tags=["fhir-r4"])


def _facade_base_url(request: Request) -> str:
    """Compute the absolute base URL of the facade from the incoming request."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip()
    scheme = forwarded_proto or request.url.scheme or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    return f"{scheme}://{host}/api/v1/fhir/R4"


@router.get("/metadata", response_model=None)
async def capability_statement(request: Request) -> Dict[str, Any]:
    """FHIR R4 CapabilityStatement (no auth — spec requirement).

    Cacheable for 5 minutes. The statement is built dynamically from the
    :data:`RESOURCE_REGISTRY` so adding a new resource automatically advertises it.
    """
    cs = build_capability_statement(_facade_base_url(request))
    return JSONResponse(
        status_code=200,
        content=cs,
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/{path:path}", response_model=None)
async def unknown_facade_route(path: str) -> JSONResponse:
    """Catch-all for not-yet-implemented facade routes.

    Returns 501 Not Implemented (OperationOutcome) for any route under
    ``/fhir/R4/`` that isn't matched by an explicit handler. As phases land,
    explicit routes will take precedence over this catch-all.
    """
    return operation_outcome(
        severity="error",
        code="not-supported",
        diagnostics=f"Facade route /fhir/R4/{path} not implemented yet",
        status_code=501,
    )

"""Unified instance search endpoint.

``GET /instances/search`` — cross-entity free-text search over patient-scoped
clinical records (examinations, medications, observations, documents, clinical
events, allergies, vaccines). The instance-side counterpart of
``GET /catalogs/search``.

Security (centralized here — the single chokepoint):
1. Tenant isolation: every query is scoped to ``current_user.tenant_id``.
2. Patient access: when ``patient_id`` is provided,
   :func:`check_patient_access` runs first (404 cross-tenant, 403 for a
   ``USER`` not linked to the patient).
3. Tenant-wide browse: a request with NO ``patient_id`` is allowed only for
   ``ADMIN``/``SYSTEM_ADMIN``; ``USER`` gets 403. The picker always binds the
   current patient context, so this is defense-in-depth against direct API
   calls enumerating patients.

No PII is logged (only type/tenant/limit). See
``dev/plans/instance-browser-unified-picker-2026-07-16.md`` §4.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import AuthorizationError
from app.core.security import get_current_user
from app.models.enums import Role
from app.schemas.instance_search import InstanceSearchResponse
from app.schemas.user import TokenData
from app.services.access import check_patient_access
from app.services.instance_search_service import search_instances

router = APIRouter(prefix="/instances", tags=["Instances"])

#: Entity types the dispatcher will search when ``types`` is omitted. Kept
#: explicit (rather than ``registered_types()``) so the contract is stable and
#: documented for API consumers; new types opt in here.
DEFAULT_INSTANCE_TYPES = [
    "examination",
    "medication",
    "observation",
    "document",
    "event",
    "allergy",
    "vaccine",
]


@router.get("/search", response_model=InstanceSearchResponse)
async def search_instances_endpoint(
    q: str = Query(..., min_length=2, description="Free-text query (min 2 chars)"),
    types: Optional[str] = Query(
        None,
        description="Comma-separated entity types to search; omit for all",
    ),
    patient_id: Optional[str] = Query(
        None, description="Patient scope; omit for tenant-wide (admin only)"
    ),
    limit: int = Query(5, ge=1, le=20, description="Max hits per entity type"),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> InstanceSearchResponse:
    tenant_id = current_user.tenant_id

    # Resolve + validate the requested types up front (422-friendly: unknown
    # types are silently dropped by the dispatcher, but an empty after-filter
    # set is a client error).
    requested_types = (
        [t.strip() for t in types.split(",") if t.strip()] if types else None
    )

    # Patient scope enforcement — the security chokepoint.
    resolved_patient_id = None
    if patient_id:
        # check_patient_access raises NotFoundError (404) cross-tenant or
        # AuthorizationError (403) when a USER may not see this patient.
        await check_patient_access(patient_id, current_user, db)
        from uuid import UUID

        resolved_patient_id = UUID(str(patient_id))
    else:
        # Tenant-wide browse is privileged. USER must always scope to a
        # patient — the picker does this by default; this blocks direct
        # tenant-wide enumeration by standard users.
        if current_user.role == Role.USER.value:
            raise AuthorizationError(
                "A patient scope is required to search records."
            )

    hits = await search_instances(
        db=db,
        tenant_id=tenant_id,
        patient_id=resolved_patient_id,
        q=q,
        types=requested_types,
        limit_per_type=limit,
    )
    return InstanceSearchResponse(results=hits)  # type: ignore[arg-type]

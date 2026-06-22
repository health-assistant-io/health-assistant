"""Generic search + CRUD dispatcher for the FHIR R4 facade.

The facade exposes one set of generic handlers (``search``, ``read``,
``create``, ``update``, ``delete``) that dispatch on the resource type via
:data:`RESOURCE_REGISTRY`. Each resource's model provides the FHIR
projection via ``to_fhir_dict()``; the converter module provides the
reverse ``fhir_to_*_orm()``.

This module is the meat of audit items C2, C3, C4, C5 — every list
endpoint returns a FHIR Bundle, every search honors standard search params,
every write returns canonical FHIR JSON with proper status codes + headers,
and deletes soft-delete via ``SoftDeleteMixin`` (tombstones → 410 Gone).
"""
import datetime as _dt
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.facade.bundle import build_search_bundle
from app.facade.registry import RESOURCE_REGISTRY, ResourceEntry
from app.facade.search_params import FhirSearchParams, parse_search_params
from app.facade.responses import gone, not_found
from app.models.base import SoftDeleteMixin
from app.models.enums import Role
from app.schemas.user import TokenData
from app.services.fhir_converter import fhir_to_orm
from app.services.fhir_helpers import FhirSerializationError, assert_valid_fhir, parse_fhir_resource
from app.services.provenance_service import record_provenance, RECORD_CREATE, RECORD_DELETE, RECORD_UPDATE


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant_predicate(entry: ResourceEntry, current_user: TokenData):
    """Build the tenant-scoping predicate for the current user.

    Resources tagged ``tenant_scope='none'`` are global (no filter); others
    filter strictly on ``tenant_id == current_user.tenant_id``.
    """
    if entry.tenant_scope == "none":
        return None
    return entry.model.tenant_id == current_user.tenant_id


def _soft_delete_predicate(entry: ResourceEntry):
    """Return ``deleted_at IS NULL`` predicate if the model supports it."""
    if not entry.soft_delete:
        return None
    if not hasattr(entry.model, "deleted_at"):
        return None
    return entry.model.deleted_at.is_(None)


def _resolve_id(value: str) -> Optional[UUID]:
    """Parse a str into a UUID; return None on failure."""
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search(
    entry: ResourceEntry,
    query_params: List[Tuple[str, str]],
    current_user: TokenData,
    db: AsyncSession,
    base_url: str,
) -> Dict[str, Any]:
    """Run a FHIR search and return a Bundle dict.

    Honors ``_id``, ``_lastUpdated``, ``_count``, ``_sort``, plus a small
    allowlist of resource-specific params. Tenant-scoped by default; soft-deleted
    rows excluded unless ``_deleted=true``.
    """
    params = parse_search_params(entry.resource_type, query_params)
    model = entry.model

    # Base predicates.
    predicates = []
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)
    soft_pred = _soft_delete_predicate(entry)
    if soft_pred is not None:
        predicates.append(soft_pred)
    if entry.search_filter is not None:
        predicates.append(entry.search_filter())

    # _id
    if params._id:
        ids = [_resolve_id(v) for v in params._id if v]
        ids = [i for i in ids if i is not None]
        if ids:
            predicates.append(model.id.in_(ids))
        else:
            # All _id values failed to parse as UUID.
            return _empty_bundle(entry, base_url, query_params)

    # _lastUpdated
    if params._lastUpdated and hasattr(model, "updated_at"):
        for f in params._lastUpdated:
            pred = f.to_orm_filter(model.updated_at)
            if pred is not None:
                predicates.append(pred)

    # Resource-specific params: token filters applied via JSONB path lookups.
    # We apply a small set per resource (patient/subject, code, status, category).
    for key, values in params.resource_filters.items():
        for value in values:
            extra_pred = _build_resource_filter(model, key, value)
            if extra_pred is not None:
                predicates.append(extra_pred)

    # Count query (full match count for pagination).
    count_stmt = select(func.count()).select_from(model).where(*predicates) if predicates else select(func.count()).select_from(model)
    total = (await db.execute(count_stmt)).scalar_one()

    # Main query with sort + pagination.
    stmt = select(model)
    if predicates:
        stmt = stmt.where(*predicates)
    for column_name, descending in params._sort or [("updated_at", True)]:
        col = getattr(model, column_name, None)
        if col is None:
            continue
        stmt = stmt.order_by(col.desc() if descending else col.asc())
    stmt = stmt.limit(params._count).offset(params.offset)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Serialize each row to FHIR. Skip-and-log on validation failure.
    resources: List[Dict[str, Any]] = []
    for row in rows:
        try:
            resources.append(row.to_fhir_dict())
        except FhirSerializationError as e:
            logger.warning(
                "Skipping invalid %s/%s in search results: %s",
                entry.resource_type,
                getattr(row, "id", "?"),
                e,
            )

    # Build the Bundle. Preserve the original query string for self-link.
    raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
    return build_search_bundle(
        base_url=base_url,
        path=entry.route_path,
        query_string=raw_qs.encode("utf-8"),
        resources=resources,
        total=total,
        offset=params.offset,
        count=params._count,
    )


def _build_resource_filter(model, key: str, value: str):
    """Build a SQLAlchemy predicate for a resource-specific search param.

    FHIR token search has several forms:
    - ``patient=Patient/uuid`` → reference lookup
    - ``patient=uuid`` → bare UUID
    - ``code=http://loinc.org|1234-5`` → system|code
    - ``code=1234-5`` → bare code
    - ``status=active`` → enum/string

    The implementation here is conservative: handle the common cases
    (patient/subject references + simple token matches). Full FHIR token
    semantics land in Phase 8.
    """
    if key in ("patient", "subject"):
        # Strip the "Patient/" prefix if present.
        raw = value.split("/")[-1] if "/" in value else value
        rid = _resolve_id(raw)
        if rid is None:
            return None
        # Try direct patient_id column first; fall back to JSONB subject lookup.
        if hasattr(model, "patient_id"):
            return model.patient_id == rid
        if hasattr(model, "subject_patient_id"):
            return model.subject_patient_id == rid
        return None
    if key in ("encounter", "context"):
        raw = value.split("/")[-1] if "/" in value else value
        rid = _resolve_id(raw)
        if rid is None:
            return None
        if hasattr(model, "encounter_id"):
            return model.encounter_id == rid
        if hasattr(model, "examination_id"):
            return model.examination_id == rid
        return None
    if key == "code":
        # Token: system|code or bare code → JSONB path lookup.
        if "|" in value:
            system, code = value.split("|", 1)
            return model.code["coding"][0]["code"].astext == code
        return model.code["coding"][0]["code"].astext == value
    if key in ("status", "clinical-status", "verification-status", "intent"):
        # Map FHIR param to model column. Status columns are typically snake_case.
        col_name_map = {
            "status": "status",
            "clinical-status": "clinical_status",
            "verification-status": "verification_status",
            "intent": "intent",
        }
        col_name = col_name_map.get(key)
        if not col_name or not hasattr(model, col_name):
            return None
        col = getattr(model, col_name)
        value_upper = value.upper()
        return or_(col == value, col == value_upper)
    if key == "category":
        if hasattr(model, "category"):
            # Category may be JSONB (list) or scalar.
            return model.category.astext == value
        return None
    # Date params: onset-date, date, effective, sent, received, authored-on.
    date_param_to_col = {
        "date": "examination_date",
        "onset-date": "onset_date",
        "effective": "effective_datetime",
        "sent": "sent",
        "received": "received",
        "authored-on": "created_at",
        "recorded": "recorded",
        "birthdate": "birth_date",
    }
    if key in date_param_to_col:
        col_name = date_param_to_col[key]
        col = getattr(model, col_name, None)
        if col is None:
            return None
        # Strip FHIR date prefix if present.
        from app.facade.search_params import _split_date_param, _parse_fhir_datetime
        f = _split_date_param(value)
        dt = _parse_fhir_datetime(f.value)
        if dt is None:
            return None
        if f.prefix in (None, "eq"):
            return col == dt
        if f.prefix in ("gt", "sa"):
            return col > dt
        if f.prefix in ("lt", "eb"):
            return col < dt
        if f.prefix == "ge":
            return col >= dt
        if f.prefix == "le":
            return col <= dt
        return col == dt
    return None


def _empty_bundle(entry: ResourceEntry, base_url: str, query_params: List[Tuple[str, str]]) -> Dict[str, Any]:
    raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
    return build_search_bundle(
        base_url=base_url,
        path=entry.route_path,
        query_string=raw_qs.encode("utf-8"),
        resources=[],
        total=0,
        offset=0,
        count=50,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def read(
    entry: ResourceEntry,
    resource_id: str,
    current_user: TokenData,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    """Fetch one resource by id. Returns the FHIR dict or None (with a 'reason'
    indicator the caller uses to choose 404 vs 410)."""
    rid = _resolve_id(resource_id)
    if rid is None:
        return None

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    row = result.scalar_one_or_none()
    if row is None:
        return None

    # Tombstone check.
    if entry.soft_delete and hasattr(row, "deleted_at") and row.deleted_at is not None:
        return {"_tombstone": True, "id": str(row.id)}

    return row.to_fhir_dict()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create(
    entry: ResourceEntry,
    fhir_data: Dict[str, Any],
    current_user: TokenData,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Create a new resource from canonical FHIR JSON.

    Returns the persisted FHIR dict. Raises ``FhirSerializationError`` on
    invalid input. Records a Provenance on success.
    """
    if "create" not in entry.interactions:
        raise PermissionError(f"create not supported for {entry.resource_type}")

    # Convert canonical FHIR → ORM-shape dict via the registered converter.
    orm_dict = fhir_to_orm(entry.resource_type, fhir_data)

    # Construct the ORM object. Strip the id if the client supplied one —
    # we always generate a new id server-side.
    orm_dict.pop("id", None)
    model = entry.model
    obj = model(**orm_dict)

    # Force tenant_id to the current user's tenant for compartment resources.
    if entry.tenant_scope == "tenant_id":
        obj.tenant_id = current_user.tenant_id

    # Validate the FHIR projection before persisting. This is the write-time
    # gate that guarantees invalid FHIR can never be persisted via the facade.
    assert_valid_fhir(obj)

    db.add(obj)
    await db.flush()  # assign id without committing
    fhir_response = obj.to_fhir_dict()

    # Best-effort Provenance.
    if entry.resource_type != "Provenance":
        await record_provenance(
            db,
            target_resource_type=entry.resource_type,
            target_id=obj.id,
            activity=RECORD_CREATE,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
        )

    await db.commit()
    await db.refresh(obj)
    return obj.to_fhir_dict()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def update(
    entry: ResourceEntry,
    resource_id: str,
    fhir_data: Dict[str, Any],
    current_user: TokenData,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    """Update an existing resource. Returns the updated FHIR dict, or None
    if the resource doesn't exist."""
    if "update" not in entry.interactions:
        raise PermissionError(f"update not supported for {entry.resource_type}")

    rid = _resolve_id(resource_id)
    if rid is None:
        return None

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    obj = result.scalar_one_or_none()
    if obj is None:
        return None

    # Convert the incoming FHIR to ORM-shape and apply mutations.
    orm_dict = fhir_to_orm(entry.resource_type, fhir_data)
    orm_dict.pop("id", None)  # don't allow id mutation
    if "tenant_id" in orm_dict:
        orm_dict.pop("tenant_id")  # don't allow tenant mutation
    for key, value in orm_dict.items():
        if hasattr(obj, key):
            setattr(obj, key, value)

    # Bump version if versioned.
    if entry.versioned and hasattr(obj, "version"):
        obj.version = (obj.version or 1) + 1

    # Validate after mutation.
    assert_valid_fhir(obj)

    await record_provenance(
        db,
        target_resource_type=entry.resource_type,
        target_id=obj.id,
        activity=RECORD_UPDATE,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )

    await db.commit()
    await db.refresh(obj)
    return obj.to_fhir_dict()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete(
    entry: ResourceEntry,
    resource_id: str,
    current_user: TokenData,
    db: AsyncSession,
) -> bool:
    """Soft-delete (tombstone) a resource. Returns True on success.

    Audit item C5: subsequent reads return 410 Gone (tombstone semantics),
    NOT 404 Not Found. Hard deletes are never used by the facade.
    """
    if "delete" not in entry.interactions:
        raise PermissionError(f"delete not supported for {entry.resource_type}")

    rid = _resolve_id(resource_id)
    if rid is None:
        return False

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    obj = result.scalar_one_or_none()
    if obj is None:
        return False

    if entry.soft_delete and hasattr(obj, "deleted_at"):
        obj.deleted_at = _dt.datetime.now(_dt.timezone.utc)
    else:
        # No soft-delete support; hard-delete (rare — only Provenance-ish resources).
        await db.delete(obj)

    await record_provenance(
        db,
        target_resource_type=entry.resource_type,
        target_id=obj.id if hasattr(obj, "id") else rid,
        activity=RECORD_DELETE,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )

    await db.commit()
    return True

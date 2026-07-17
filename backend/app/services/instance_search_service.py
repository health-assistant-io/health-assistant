"""Unified instance search dispatcher.

Fans a free-text query out across one or more patient-scoped entity types via
the registry (:mod:`app.instances.registry`), fusing the per-type hit lists.
This is the instance-side counterpart of the catalog ``search_catalogs``
dispatcher.

Security: this dispatcher performs NO access checks itself — it trusts the
``tenant_id`` (and optional ``patient_id``) handed to it by the HTTP endpoint,
which is the single chokepoint that enforces them (``check_patient_access`` +
the USER-tenant-wide 403 gate). Each registered per-entity search function
applies the tenant (+ patient) filter in its own SQL, so a type can't forget
to scope. The cross-tenant negative test (``test_instances_endpoint``) gates
this module's merge.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

import app.instances  # noqa: F401 — triggers per-entity registration
from app.instances.registry import get_instance_search, registered_types


async def search_instances(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    types: Optional[list[str]] = None,
    limit_per_type: int = 5,
) -> list[dict]:
    """Search across instance entity types.

    Args:
        db: the request/session DB.
        tenant_id: mandatory tenant scope.
        patient_id: optional patient scope (None = tenant-wide; the endpoint
            only allows that for ADMIN/SYSTEM_ADMIN).
        q: free-text query (the endpoint enforces min length).
        types: restrict to these entity types; None/empty = all registered.
        limit_per_type: cap per entity type before fusion.

    Returns a flat list of hit dicts (``{type, id, label, subtitle, date}``),
    type-grouped in registration order. The endpoint wraps this in the
    response schema.
    """
    requested = types if types else registered_types()
    hits: list[dict] = []
    for entity_type in requested:
        try:
            fn = get_instance_search(entity_type)
        except KeyError:
            # Unknown type requested — skip rather than 500 (forward-compat).
            continue
        try:
            type_hits = await fn(db, tenant_id, patient_id, q, limit_per_type)
            hits.extend(type_hits)
        except Exception:  # noqa: BLE001 — one type failing must not break others
            # A single entity type erroring (e.g. a missing column after a
            # partial migration) must not blank out the whole search. Log via
            # the dispatcher's module logger and continue.
            import logging

            logging.getLogger(__name__).warning(
                "instance_search: type '%s' raised; skipping", entity_type
            )
            continue
    return hits

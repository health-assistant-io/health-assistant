"""The telemetry ingest seam: slug registry, device resolution, canonical units.

Every writer that produces ``telemetry_data`` rows goes through this module —
the mobile upload endpoint, the integration sync split, FHIR→telemetry
migration, and backup restore. That keeps three invariants in one place:

* **slug registry** — an ingested slug must resolve to a visible
  ``BiomarkerDefinition`` flagged ``is_telemetry``; an unknown slug is a typed
  rejection, never a free-string row (constitution rule 8: a new metric is a
  row, not a table, but it still has to be declared);
* **device identity** — the client's opaque device reference resolves to a
  ``telemetry_devices`` UUID scoped by tenant + patient + source;
* **canonical units** — the stored ``value``/``unit`` pair is canonical while
  ``raw_value``/``raw_unit`` preserve provenance.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition
from app.models.telemetry_model import TelemetryDeviceModel
from app.services.biomarkers.unit_conversion import canonicalize_measurement

logger = logging.getLogger(__name__)


class UnknownTelemetrySlugError(ValueError):
    """Raised when an ingest row names a slug no telemetry biomarker declares."""


async def resolve_telemetry_definition(
    db: AsyncSession,
    tenant_id: UUID,
    slug: str,
) -> BiomarkerDefinition:
    """Return the visible telemetry biomarker for ``slug`` or raise.

    Visible means the tenant's own definition or a global (system) one. A
    definition that exists but is not telemetry-flagged is rejected too — a
    metric must be declared as telemetry before device readings may land in
    the hypertable.
    """
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.slug == slug,
            or_(
                BiomarkerDefinition.tenant_id == tenant_id,
                BiomarkerDefinition.tenant_id.is_(None),
            ),
        )
    )
    definition = result.scalars().first()
    if definition is None:
        raise UnknownTelemetrySlugError(f"Unknown telemetry biomarker slug: {slug!r}")
    if not definition.is_telemetry:
        raise UnknownTelemetrySlugError(
            f"Biomarker {slug!r} is not telemetry-flagged and cannot accept device data"
        )
    return definition


async def resolve_telemetry_definition_map(
    db: AsyncSession,
    tenant_id: UUID,
    slugs: set[str],
) -> dict[str, BiomarkerDefinition]:
    """Resolve a batch of slugs, raising on the first unknown/non-telemetry one."""
    definitions: dict[str, BiomarkerDefinition] = {}
    for slug in sorted(slugs):
        definitions[slug] = await resolve_telemetry_definition(db, tenant_id, slug)
    return definitions


async def resolve_telemetry_device(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    patient_id: UUID,
    source: str,
    external_ref: str,
    label: str | None = None,
) -> UUID:
    """Get-or-create the ``telemetry_devices`` row and return its id.

    Identity is ``(tenant_id, patient_id, source, external_ref)``. The INSERT
    is conflict-safe, so concurrent syncs of the same device converge on one
    row instead of racing the unique key.
    """
    stmt = (
        pg_insert(TelemetryDeviceModel)
        .values(
            tenant_id=tenant_id,
            patient_id=patient_id,
            source=source,
            external_ref=external_ref,
            label=label or external_ref,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "patient_id", "source", "external_ref"]
        )
        .returning(TelemetryDeviceModel.id)
    )
    device_id = (await db.execute(stmt)).scalar_one_or_none()
    if device_id is not None:
        return device_id

    existing = await db.execute(
        select(TelemetryDeviceModel.id).where(
            TelemetryDeviceModel.tenant_id == tenant_id,
            TelemetryDeviceModel.patient_id == patient_id,
            TelemetryDeviceModel.source == source,
            TelemetryDeviceModel.external_ref == external_ref,
        )
    )
    found = existing.scalar_one_or_none()
    if found is None:
        raise RuntimeError(f"telemetry device race for {external_ref!r} in tenant {tenant_id}")
    return found


def canonical_measurement(
    *,
    definition: BiomarkerDefinition,
    value: float,
    unit: str | None,
) -> tuple[float, str | None]:
    """Canonicalize one measurement against the definition's preferred unit."""
    preferred = None
    preferred_unit = getattr(definition, "preferred_unit", None)
    if preferred_unit is not None:
        symbol = getattr(preferred_unit, "symbol", None)
        if isinstance(symbol, str):
            preferred = symbol
    return canonicalize_measurement(
        slug=getattr(definition, "slug", None),
        value=value,
        unit=unit,
        preferred_unit=preferred,
    )


def build_telemetry_row(
    *,
    tenant_id: UUID,
    patient_id: UUID,
    device_id: UUID,
    timestamp: Any,
    definition: BiomarkerDefinition,
    value: float,
    unit: str | None,
) -> dict[str, Any]:
    """Build the canonical+provenance column dict for one telemetry row."""
    canonical_value, canonical_unit = canonical_measurement(
        definition=definition, value=value, unit=unit
    )
    return {
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "device_id": device_id,
        "timestamp": timestamp,
        "slug": definition.slug,
        "value": canonical_value,
        "unit": canonical_unit,
        "raw_value": value,
        "raw_unit": unit,
    }

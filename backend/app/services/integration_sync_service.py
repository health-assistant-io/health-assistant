"""Integration sync helper.

Centralizes the FHIR/telemetry split logic that lives at the boundary of:

  - background task ``sync_active_integrations``
  - manual sync endpoint at ``POST /integrations/{id}/sync``
  - webhook delivery endpoint
  - bridge provider

The split is keyed on ``BiomarkerDefinition.is_telemetry``: Observations
linked to a telemetry-flagged biomarker are routed to the TimescaleDB
hypertable (``telemetry_data``); the rest are persisted as FHIR rows.
"""
import logging
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel

logger = logging.getLogger(__name__)


def _obs_value(obs: Observation) -> Optional[float]:
    """Best-effort numeric extraction for telemetry column mapping."""
    val = getattr(obs, "normalized_value", None)
    if val is None:
        val = getattr(obs, "raw_value", None)
    if val is None and obs.value_quantity:
        val = obs.value_quantity.get("value")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_biomarker_definitions(
    db: AsyncSession, observations: List[Observation]
) -> dict:
    """Bulk-load the BiomarkerDefinition rows referenced by ``observations``.

    Returns a dict ``{biomarker_id: BiomarkerDefinition}``.
    """
    b_ids = list({obs.biomarker_id for obs in observations if obs.biomarker_id})
    if not b_ids:
        return {}
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
    )
    return {b.id: b for b in result.scalars().all()}


async def apply_telemetry_split(
    db: AsyncSession,
    observations: List[Observation],
    tenant_id: UUID | str | None,
    instance_name: Optional[str],
    provider_name: str,
    integration_id: Optional[UUID | str] = None,
) -> Tuple[List[TelemetryDataModel], List[Observation]]:
    """Apply the FHIR/telemetry split in-memory and queue both row types on ``db``.

    Returns ``(telemetry_records, fhir_records)``. The caller is responsible
    for committing ``db`` once both batches are added.
    """
    if not observations:
        return [], []

    b_defs_map = await fetch_biomarker_definitions(db, observations)

    telemetry_records: List[TelemetryDataModel] = []
    fhir_records: List[Observation] = []

    device_id = instance_name or provider_name

    for obs in observations:
        is_telemetry = False
        if obs.biomarker_id and obs.biomarker_id in b_defs_map:
            is_telemetry = bool(b_defs_map[obs.biomarker_id].is_telemetry)

        if is_telemetry:
            b_def = b_defs_map[obs.biomarker_id]
            slug = (b_def.slug or "").lower()
            value = _obs_value(obs)

            hr = steps = cal = None
            data_payload: dict = {}

            if "8867-4" in slug or "heart-rate" in slug or slug == "heart_rate":
                hr = value
            elif "41950-7" in slug or "steps" in slug:
                steps = value
            elif "calories" in slug:
                cal = value
            else:
                data_payload[slug] = value
                if obs.value_quantity:
                    data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "")

            telemetry_records.append(
                TelemetryDataModel(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    timestamp=obs.effective_datetime,
                    heart_rate=hr,
                    steps=steps,
                    calories=cal,
                    data=data_payload if data_payload else None,
                )
            )
        else:
            if not obs.performer:
                reference = (
                    f"Integration/{integration_id}" if integration_id else None
                )
                performer = {
                    "type": "Integration",
                    "display": device_id,
                }
                if reference:
                    performer["reference"] = reference
                obs.performer = [performer]
            fhir_records.append(obs)

    if telemetry_records:
        db.add_all(telemetry_records)
    if fhir_records:
        db.add_all(fhir_records)

    return telemetry_records, fhir_records

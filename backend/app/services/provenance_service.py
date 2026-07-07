"""Service for recording FHIR Provenance resources on facade writes.

Audit item C10: every facade create/update/delete produces a Provenance
resource recording who/when/why. Provenance is **immutable** — once written,
it never changes.

This is a best-effort service: if Provenance creation fails, the resource
write is NOT rolled back (spec allows this). We log a warning and continue.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fhir.provenance import (
    ACTIVITY_CREATE,
    ACTIVITY_DELETE,
    ACTIVITY_SYSTEM,
    ACTIVITY_UPDATE,
    ProvenanceModel,
)


logger = logging.getLogger(__name__)


def _activity_concept(code: str) -> Dict[str, Any]:
    """Build a CodeableConcept for the Provenance.activity field."""
    return {
        "coding": [
            {
                "system": ACTIVITY_SYSTEM,
                "code": code,
            }
        ]
    }


async def _agent_block(
    db: AsyncSession,
    user_id: Optional[UUID],
    tenant_id: Optional[UUID],
    integration_id: Optional[UUID] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Build the agent[] block for a Provenance.

    F12: ``Provenance.agent.who`` must reference a real FHIR resource type
    (Practitioner / PractitionerRole / RelatedPerson / Patient / Device /
    Organization). The previous implementation emitted ``User/<id>`` and
    ``Integration/<id>`` — neither is a FHIR resource, so external clients
    resolving the reference got 404.

    Resolution:
    - ``user_id`` → ``Practitioner/<doctor.id>`` via DoctorModel.user_id.
      If the user has no Doctor row (admin/manager), fall back to a
      ``{display: "..."}`` form (spec-compliant — no `reference`).
    - ``integration_id`` → ``Device/<device.id>`` via
      DeviceModel.owner_integration_id. If no Device row exists, fall back
      to ``{display: "..."}``.

    Returns ``(agents, degraded)`` where ``degraded`` is True if any agent
    was emitted in the display-only shape (so the caller can tag the
    Provenance with a "degraded" meta tag if desired).
    """
    agents: List[Dict[str, Any]] = []
    degraded = False

    if integration_id:
        device_ref = await _resolve_device_ref(db, integration_id)
        if device_ref is not None:
            who_ref: Optional[Dict[str, str]] = {"reference": device_ref}
        else:
            who_ref = {"display": f"Integration {integration_id} (no Device row)"}
            degraded = True
        agents.append(
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            "code": "author",
                            "display": "Author",
                        }
                    ]
                },
                "who": who_ref,
            }
        )

    if user_id:
        practitioner_ref = await _resolve_practitioner_ref(db, user_id, tenant_id)
        if practitioner_ref is not None:
            who_ref = {"reference": practitioner_ref}
        else:
            who_ref = {"display": f"User {user_id} (no Practitioner row)"}
            degraded = True
        agents.append(
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            "code": "author",
                            "display": "Author",
                        }
                    ]
                },
                "who": who_ref,
            }
        )

    if not agents:
        # No identifiable agent at all — record an anonymous display-only agent.
        agents.append(
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            "code": "author",
                            "display": "Author",
                        }
                    ]
                },
                "who": {"display": "Unknown (no authenticated user)"},
            }
        )
        degraded = True

    return agents, degraded


async def _resolve_practitioner_ref(
    db: AsyncSession, user_id: UUID, tenant_id: Optional[UUID]
) -> Optional[str]:
    """Resolve a User id to a ``Practitioner/<id>`` reference via
    DoctorModel.user_id. Returns None if the user has no Doctor row."""
    from sqlalchemy import select

    from app.models.doctor_model import DoctorModel

    try:
        query = select(DoctorModel.id).where(DoctorModel.user_id == user_id)
        if tenant_id is not None:
            query = query.where(DoctorModel.tenant_id == tenant_id)
        result = await db.execute(query)
        doctor_id = result.scalar_one_or_none()
        if doctor_id is None:
            return None
        return f"Practitioner/{doctor_id}"
    except Exception as e:
        logger.warning("Practitioner resolution failed for user_id=%s: %s", user_id, e)
        return None


async def _resolve_device_ref(db: AsyncSession, integration_id: UUID) -> Optional[str]:
    """Resolve a UserIntegration id to a ``Device/<id>`` reference via
    DeviceModel.owner_integration_id. Returns None if no Device row exists."""
    from sqlalchemy import select

    from app.models.fhir.device import DeviceModel

    try:
        result = await db.execute(
            select(DeviceModel.id).where(
                DeviceModel.owner_integration_id == integration_id
            )
        )
        device_id = result.scalar_one_or_none()
        if device_id is None:
            return None
        return f"Device/{device_id}"
    except Exception as e:
        logger.warning(
            "Device resolution failed for integration_id=%s: %s",
            integration_id,
            e,
        )
        return None


async def record_provenance(
    db: AsyncSession,
    *,
    target_resource_type: str,
    target_id: UUID,
    activity: str,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    integration_id: Optional[UUID] = None,
    client_id: Optional[str] = None,
    entity_inputs: Optional[List[Dict[str, Any]]] = None,
) -> Optional[ProvenanceModel]:
    """Record a Provenance resource for a single target.

    Args:
        db: an open async session (the caller manages the transaction).
        target_resource_type: FHIR resource type (e.g. ``"Condition"``).
        target_id: the resource UUID.
        activity: ``"CREATE"``, ``"UPDATE"``, or ``"DELETE"``.
        tenant_id: tenant scope (must match the target's tenant).
        user_id: the user performing the action (None for integration/system actions).
        integration_id: the integration performing the action (None for user actions).
        entity_inputs: optional list of source entities (e.g. the source
            DocumentReference for an extracted biomarker).

    Returns:
        The created ProvenanceModel, or None if recording failed (best-effort).
    """
    try:
        agents, degraded = await _agent_block(
            db, user_id=user_id, tenant_id=tenant_id, integration_id=integration_id
        )
        # G10: when the caller is a service account, record the external
        # system identity as an additional agent (display-only — the SA's
        # client_id has no FHIR resource to reference).
        if client_id:
            agents.append(
                {
                    "who": {"display": f"Service Account: {client_id}"},
                    "type": [
                        {
                            "coding": [
                                {
                                    "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                                    "code": "author",
                                }
                            ]
                        }
                    ],
                }
            )
        provenance = ProvenanceModel(
            tenant_id=tenant_id,
            target=[{"reference": f"{target_resource_type}/{target_id}"}],
            recorded=datetime.now(timezone.utc),
            activity=_activity_concept(activity),
            agent=agents,
            entity=entity_inputs,
        )
        if degraded:
            # F12: log that we couldn't resolve the agent to a real FHIR
            # resource type. The agent is still spec-compliant (a display-
            # only Reference), but consumers can grep the log if they need
            # to identify gaps in the audit trail.
            logger.info(
                "Provenance for %s/%s (activity=%s) has a degraded agent "
                "(no Practitioner/Device row for the actor)",
                target_resource_type,
                target_id,
                activity,
            )
        # Validate the FHIR shape before persisting. A failed Provenance
        # should not abort the parent resource write — we log + return None.
        try:
            provenance.to_fhir_dict()
        except Exception as validation_err:
            logger.warning(
                "Provenance FHIR validation failed for %s/%s (activity=%s): %s",
                target_resource_type,
                target_id,
                activity,
                validation_err,
            )
            # Persist anyway with a degraded shape (drop entity if it caused the issue).
            provenance.entity = None

        db.add(provenance)
        await db.flush()  # assign id without committing the parent transaction
        return provenance
    except Exception as e:
        logger.error(
            "Failed to record Provenance for %s/%s (activity=%s): %s",
            target_resource_type,
            target_id,
            activity,
            e,
            exc_info=True,
        )
        return None


# Activity code constants exported for callers.
RECORD_CREATE = ACTIVITY_CREATE
RECORD_UPDATE = ACTIVITY_UPDATE
RECORD_DELETE = ACTIVITY_DELETE

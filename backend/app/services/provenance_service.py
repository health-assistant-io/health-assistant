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


def _agent_block(
    user_id: Optional[UUID],
    integration_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """Build the agent[] block for a Provenance.

    The agent identifies who performed the action. We support two modes:
    - User agent (typical case): ``{who: {reference: "User/<id>"}}``
    - Integration agent (system-to-system): ``{who: {reference: "Integration/<id>"}}``

    Both use the Provenance agent type "author" (HL7 ProvenanceParticipantType).
    """
    who_ref: Optional[Dict[str, str]] = None
    if integration_id:
        who_ref = {"reference": f"Integration/{integration_id}"}
    elif user_id:
        who_ref = {"reference": f"User/{user_id}"}

    if who_ref is None:
        # No identifiable agent — record an anonymous device-like agent.
        who_ref = {"display": "Unknown (no authenticated user)"}

    return [
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
    ]


async def record_provenance(
    db: AsyncSession,
    *,
    target_resource_type: str,
    target_id: UUID,
    activity: str,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    integration_id: Optional[UUID] = None,
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
        provenance = ProvenanceModel(
            tenant_id=tenant_id,
            target=[{"reference": f"{target_resource_type}/{target_id}"}],
            recorded=datetime.now(timezone.utc),
            activity=_activity_concept(activity),
            agent=_agent_block(user_id, integration_id),
            entity=entity_inputs,
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

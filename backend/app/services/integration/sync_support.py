"""Shared integration-sync constants, the capability gate and SyncResult.

Moved verbatim out of integration_sync_service (plan 40 W2d) so the
legs, the notification emitters and the orchestrator share one home."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.extensions.registry import integration_registry

# Cap on how many catalog proposals a single sync will apply. Excess items
# are dropped with a warning so a runaway provider can't spam the catalog.
# Cross-cutting env-var-ification is deferred (see parent plan §D.3).
INTEGRATION_MAX_PROPOSALS_PER_SYNC = 50

# Cap on how many HITL proposals a single sync will queue. Excess items
# are dropped with a warning so a runaway provider can't spam the inbox.
INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC = 20

_CHARTER_CAPABILITIES = frozenset({"push", "notify"})


@dataclass
class SyncResult:
    """Structured result of one integration sync run.

    The caller (worker or manual-sync endpoint) inspects ``status`` and
    ``error_type`` to decide how to surface the outcome (log + continue for
    the worker; raise an HTTPException for the endpoint).
    """

    pulled: int = 0
    fhir_persisted: int = 0
    telemetry_persisted: int = 0
    dropped_invalid: int = 0
    pushed: dict[str, Any] | None = None
    status: str = "success"  # "success" | "partial" | "failed" | "skipped"
    error: str | None = None
    error_type: str | None = None  # "auth" | "rate_limit" | "data" | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    proposals_pulled: int = 0
    proposals_applied: int = 0
    hitl_proposals_pulled: int = 0
    hitl_proposals_inserted: int = 0
    documents_pulled: int = 0
    documents_written: int = 0
    # Phase 4 (Route A) treatment-resource pulls.
    medications_pulled: int = 0
    medications_written: int = 0
    allergies_pulled: int = 0
    allergies_written: int = 0
    immunizations_pulled: int = 0
    immunizations_written: int = 0
    # Labs-stream pull (plan 8 W4): DiagnosticReport payload dicts through
    # the ingest funnel.
    labs_pulled: int = 0
    labs_written: int = 0
    # Carried from the typed rate-limited error's retry_after_ms when
    # the upstream sent a ``Retry-After`` header. ``run_sync`` writes it
    # into ``integration_sync_state.next_retry_at`` (backoff is data); the
    # beat reads that row instead of a code-embedded cooldown.
    retry_after_seconds: float | None = None


def _declared(provider: Any, capability: str) -> bool:
    """Declared-capability gate (D3/D18 — replaces hook probing).

    ``capability`` is either a charter capability id (``push`` / ``notify``)
    or a declared data class (one ``itemSchema`` property the connector kind
    may pull). The ANSWER always comes from the registry: an undeclared
    capability is never called — capabilities gate at registration, never
    mid-run.
    """
    domain = getattr(provider, "domain", None) or getattr(
        getattr(provider, "integration", None), "provider", None
    )
    if not domain:
        return False
    if capability in _CHARTER_CAPABILITIES:
        return integration_registry.has_capability(domain, capability)
    return capability in integration_registry.declared_data_classes(domain)

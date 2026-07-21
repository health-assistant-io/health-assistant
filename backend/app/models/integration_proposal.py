from sqlalchemy import (
    Column,
    String,
    Text,
    ForeignKey,
    DateTime,
    Enum,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    TimestampMixin,
)
from app.models.enums import HitlTaskStatus


class IntegrationProposal(
    Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin
):
    """A pending catalog write proposed by an integration, awaiting human
    review.

    Workstream G of the integrations follow-ups pass. Providers opt in via
    ``BaseHealthProvider.supports_hitl_proposals`` and emit
    ``IntegrationProposalSpec`` objects through ``pull_hitl_proposals``. The
    engine persists each as a PROPOSED row + fires an HITL notification;
    the user reviews via ``/api/v1/integrations/instance/{id}/proposals/...``
    and resolves (approve / reject / cancel). On approve, the resolver
    delegates to ``catalog_proposal_service.apply_proposal`` — the same
    write path F.3 auto-applies through — so the catalog stays consistent
    regardless of how a proposal entered the system.

    Dedup: ``dedup_key`` = sha256 of canonical JSON of
    ``(proposal_type, proposed_payload)``. A partial unique index on
    ``(integration_id, dedup_key)`` (where ``dedup_key IS NOT NULL``) makes
    re-propose idempotent. Providers wanting stronger "don't re-propose
    after decision" semantics should advance their own cursor in
    ``handle_proposal_resolution``.
    """

    __tablename__ = "integration_proposals"

    integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        # CASCADE per audit D9 (every patient_id FK must cascade so a
        # patient's clinical record deletes atomically). Tenant-wide
        # catalog proposals leave patient_id NULL — CASCADE doesn't
        # affect them.
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
    )

    proposal_type = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    status = Column(
        Enum(
            HitlTaskStatus,
            name="integration_proposal_status",
            # ``HitlTaskStatus`` has name != value (PROPOSED vs "proposed").
            # The PG enum type was created with the lowercase *values* (matching
            # the chat-side HITL JSONB shape), so the Python layer must emit
            # values too. Without this, SQLAlchemy would send the uppercase
            # *names* and Postgres would reject them.
            values_callable=lambda enum_cls: [v.value for v in enum_cls],
        ),
        default=HitlTaskStatus.PROPOSED,
        nullable=False,
        index=True,
    )

    proposed_payload = Column(JSONB, nullable=False)
    context = Column(JSONB, nullable=True, default=dict)

    resolved_payload = Column(JSONB, nullable=True)
    resolved_by = Column(PG_UUID(as_uuid=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)

    # Best-effort idempotency key: sha256 hex of
    # canonical_json({"type": proposal_type, "payload": proposed_payload}).
    # NULL when the proposal type is unknown / can't be canonicalized — those
    # proposals are non-dedupable and always insert.
    dedup_key = Column(String(64), nullable=True, index=True)

    integration = relationship("UserIntegration")

    __table_args__ = (
        # List-by-status endpoint: WHERE integration_id = ? AND status = ?
        Index(
            "ix_integration_proposals_integration_status",
            "integration_id",
            "status",
        ),
        # Tenant-wide queries (admin dashboards, future cleanup jobs).
        Index(
            "ix_integration_proposals_tenant_status",
            "tenant_id",
            "status",
        ),
        # Idempotent propose: a re-sync emitting the same payload for the
        # same integration must not produce a duplicate row. Partial
        # (dedup_key IS NOT NULL) so legacy / non-canonicalizable rows are
        # exempt. Enforced at the DB layer so a racing beat task can't
        # double-insert even with the engine's lookup-then-insert pattern.
        Index(
            "uq_integration_proposals_integration_dedup",
            "integration_id",
            "dedup_key",
            unique=True,
            postgresql_where="dedup_key IS NOT NULL",
        ),
    )

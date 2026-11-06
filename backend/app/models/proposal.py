"""The one HITL proposal store (schema plan 5, ADR-0015 Class-B).

``proposal_kinds``
    The *extensible domain vocabulary* (constitution rule 7b) behind
    ``proposals.kind_id``. Rows are seeded at boot from the family contract
    (``app/extensions/hitl-proposal-kinds.json``, canonical in
    ``the family contract canonical/``) — seeding is data, not DDL, so a new proposal kind is
    a contract entry plus a row, never a migration. Deliberately **no CHECK**
    on the kind: contract JSON + CHECK + code would be three sources of
    truth. Rows are never deleted while referenced (FK ``RESTRICT``).

``proposals``
    One table for every AI-proposed write, whatever its origin: chat HITL
    cards, extension ``pull_hitl_proposals`` specs, and automation jobs.
    ``source`` + ``source_ref`` name the origin (a chat message, an
    integration instance, a background job); ``ordinal`` preserves card order
    within one origin, so message→proposals is a query, never a JSONB array
    column. ``kind_id`` names the draft's kind, ``payload`` holds the proposal
    as declared (a ``payload_schema_version``-versioned card document),
    ``final_payload`` the user-approved shape (validated against the kind's
    schema before any write — model output is untrusted), and
    ``resolution_trace`` the audit-grade who/what/when of every transition.

    Lifecycle: ``operational`` — proposals are bounded-retention work items;
    the expiry sweep (``app.workers.tasks.expire_proposals``) resolves stale
    rows to ``expired``. Erasure: ``tenant_id``/``patient_id`` cascade on
    tenant/patient deletion; ``user_id`` is a plain column so the audit row
    survives user deletion.

    ``status`` is the closed behavioral state machine (seven values, CHECK-
    mirrored, transitions enforced in one module —
    :mod:`app.services.proposals.proposal_state`).
"""

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import text

from app.models.enums import ProposalSource, ProposalStatus

from .base import (
    AuditMixin,
    Base,
    TenantMixin,
    TimestampMixin,
    UserMixin,
    UUIDMixin,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class ProposalKind(Base, TimestampMixin):
    """One HITL proposal kind in the seeded vocabulary.

    ``id`` is deterministic (uuid5 over ``code`` — see
    :func:`app.extensions.proposal_kinds.proposal_kind_id`) so the boot
    seeder is idempotent and a proposal row's FK target is derivable without
    a lookup. ``schema_ref`` names the pydantic model that validates a
    ``final_payload`` before any write; ``resolver`` names the server-side
    apply path; ``card_variant`` is the ADR-0015 wire card key the chat UI
    registers a handler under.
    """

    __tablename__ = "proposal_kinds"

    id = Column(UUID(as_uuid=True), primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    tier = Column(SmallInteger, nullable=False)
    schema_ref = Column(String(160), nullable=False)
    resolver = Column(String(64), nullable=False)
    card_variant = Column(String(64), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (CheckConstraint("tier BETWEEN 1 AND 2", name="tier_allowed"),)


class Proposal(Base, UUIDMixin, TenantMixin, UserMixin, AuditMixin, TimestampMixin):
    """One AI-proposed write awaiting (or carrying) human resolution."""

    __tablename__ = "proposals"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source = Column(
        SQLEnum(
            ProposalSource,
            name="proposal_source",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    source_ref = Column(UUID(as_uuid=True), nullable=True, index=True)
    ordinal = Column(Integer, nullable=True)
    kind_id = Column(
        UUID(as_uuid=True),
        ForeignKey("proposal_kinds.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(
        SQLEnum(
            ProposalStatus,
            name="proposal_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=ProposalStatus.PROPOSED,
        server_default=text("'proposed'"),
        index=True,
    )
    payload = Column(JSONB, nullable=False)
    payload_schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    final_payload = Column(JSONB, nullable=True)
    dedup_key = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    resolution_trace = Column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    superseded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("proposals.id", ondelete="SET NULL"),
        nullable=True,
    )

    kind = relationship("ProposalKind", lazy="joined")

    __table_args__ = (
        Index("ix_proposals_source_ref_ordinal", "source", "source_ref", "ordinal"),
        Index("ix_proposals_tenant_status", "tenant_id", "status"),
        Index("ix_proposals_patient_status", "patient_id", "status"),
        Index(
            "uq_proposals_tenant_dedup",
            "tenant_id",
            "dedup_key",
            unique=True,
            postgresql_where="dedup_key IS NOT NULL",
        ),
    )

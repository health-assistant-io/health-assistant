from sqlalchemy import (
    UUID,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base, SystemScopeTenantMixin, UUIDMixin


class AuditEvent(Base, UUIDMixin, SystemScopeTenantMixin):
    """One immutable audit-stream event (identity-auth §17 / plan 16 H2).

    Normative shape: actor (``user_id``), action, resource
    (``resource_type``/``resource_id``), tenant, outcome, timestamp
    (``created_at``), plus the two sanctioned product additions holding the
    redacted write diff (before/after JSONB — PHI is stripped at write time
    by ``app.services.audit.redaction``).

    Tamper-evidence (ADR 0054 D3, program Phase 4A): every row extends a
    per-tenant hash chain — ``chain_key`` names the chain (``"system"`` for
    the NULL-tenant chain), ``seq`` is the 1-based position, ``prev_hash``
    links to the previous row's ``entry_hash`` (genesis: 64 zeros) and
    ``entry_hash`` is the canonical SHA-256 over the immutable fields.
    ``(chain_key, seq)`` is unique — a deleted middle row leaves a detectable
    gap, not a renumbered tail.

    Retention: append-only tombstone — rows are never updated by the app;
    the sanctioned lifecycle flows (data-subject erasure, 3650-day
    anonymize) rewrite only the mutable payload columns (the actor and the
    two diff columns), which sit outside the hash so the chain stays
    verifiable.
    """

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("chain_key", "seq", name="uq_audit_events_chain_key_seq"),)

    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    outcome = Column(String(20), nullable=False, server_default="ok", index=True)
    old_value = Column(JSONB, nullable=True)  # gate-allow: *, audit diff
    new_value = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    chain_key = Column(String(64), nullable=False)
    seq = Column(BigInteger, nullable=False)
    prev_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False)


class AuditChainHead(Base):
    """Append position + head-hash anchor for one audit chain.

    One row per ``chain_key`` (``"system"``, or ``"t:<tenant_id>"`` for a
    tenant chain). Appends take a row-level lock on this row
    (``SELECT ... FOR UPDATE``), extend the sequence and advance the head
    hash in the same transaction — concurrent appends of one chain serialize
    on the head row, never on the ``audit_events`` table.

    ``tenant_id`` mirrors the chain's tenant purely for the ``ON DELETE
    CASCADE``: deleting a tenant removes its audit rows (pre-existing FK
    cascade) and its head row together, so a dead tenant's chain vanishes as
    a whole instead of surviving as a permanent gap alarm.
    """

    __tablename__ = "audit_chain_heads"

    chain_key = Column(String(64), primary_key=True)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    seq = Column(BigInteger, nullable=False, default=0, server_default="0")
    head_hash = Column(String(64), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

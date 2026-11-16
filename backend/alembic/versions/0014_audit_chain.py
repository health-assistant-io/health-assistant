"""Audit hash chain (ADR 0054 D3 — program Phase 4A, part A).

Adds tamper-evidence to ``audit_events``: ``chain_key`` (``"system"`` for
the NULL-tenant chain, ``"t:<tenant_id>"`` for a tenant chain — a row-level
key instead of a ``(tenant_id, seq)`` uniqueness so the system chain has no
NULL-distinct holes), ``seq`` (1-based per chain), ``prev_hash`` (genesis:
64 zeros) and ``entry_hash`` (canonical SHA-256 over the immutable fields,
recomputed by ``app.services.audit.audit_chain``). ``(chain_key, seq)`` is
unique — a deleted middle row leaves a detectable gap.

New table ``audit_chain_heads`` — one row per chain, the append
coordination point: writers take a row-level lock on the head row (never on
the audit table) and advance ``seq``/``head_hash`` in the append
transaction. ``tenant_id`` mirrors the chain's tenant purely for the
``ON DELETE CASCADE`` so a deleted tenant's chain vanishes whole (its audit
rows already cascade on the same event) instead of surviving as a
permanent gap alarm.

Existing rows are backfilled: per chain, rows are sequenced in
``(created_at, id)`` order, hashed with the same canonical function the
appender uses, and the head row is created at the last position. The
backfill is idempotent-by-shape (runs once, before the NOT NULL +
unique-constraint tightening).

Downgrade drops the constraint, the columns and the head table (the
program has no production users; the chain is re-derived from scratch on
re-upgrade).

Revision ID: 0014_audit_chain
Revises: 0013_patient_access_grants
Create Date: 2026-11-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op
from app.services.audit.audit_chain import (
    GENESIS_HASH,
    chain_key_for,
    compute_entry_hash,
)

# revision identifiers, used by Alembic.
revision: str = "0014_audit_chain"
down_revision: str | Sequence[str] | None = "0013_patient_access_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill(bind: sa.engine.Connection) -> None:
    """Sequence + hash the pre-chain rows, one head per chain."""
    rows = bind.execute(
        text(
            "SELECT id, tenant_id, action, resource_type, resource_id, outcome, created_at "
            "FROM audit_events "
            "ORDER BY tenant_id NULLS FIRST, created_at, id"
        )
    ).fetchall()

    seq = 0
    prev_hash = GENESIS_HASH
    current_key: str | None = None
    current_tenant: object = None
    updates: list[dict] = []
    heads: list[dict] = []

    def _close_chain() -> None:
        if current_key is not None and seq > 0:
            heads.append(
                {
                    "chain_key": current_key,
                    "tenant_id": current_tenant,
                    "seq": seq,
                    "head_hash": prev_hash,
                }
            )

    for row in rows:
        key = chain_key_for(row.tenant_id)
        if key != current_key:
            _close_chain()
            current_key = key
            current_tenant = row.tenant_id
            seq = 0
            prev_hash = GENESIS_HASH
        seq += 1
        entry_hash = compute_entry_hash(
            chain_key=key,
            seq=seq,
            prev_hash=prev_hash,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            outcome=row.outcome,
            created_at=row.created_at,
        )
        updates.append(
            {
                "chain_key": key,
                "seq": seq,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "id": str(row.id),
            }
        )
        prev_hash = entry_hash
    _close_chain()

    if updates:
        bind.execute(
            text(
                "UPDATE audit_events SET chain_key = :chain_key, seq = :seq, "
                "prev_hash = :prev_hash, entry_hash = :entry_hash WHERE id = :id"
            ),
            updates,
        )
    if heads:
        bind.execute(
            text(
                "INSERT INTO audit_chain_heads (chain_key, tenant_id, seq, head_hash) "
                "VALUES (:chain_key, :tenant_id, :seq, :head_hash) "
                "ON CONFLICT (chain_key) DO NOTHING"
            ),
            heads,
        )


def upgrade() -> None:
    """Add the chain columns, the head table, then backfill and tighten."""
    op.add_column("audit_events", sa.Column("chain_key", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("entry_hash", sa.String(64), nullable=True))
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain_key", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("head_hash", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_audit_chain_heads_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chain_key", name=op.f("pk_audit_chain_heads")),
    )
    op.create_index(
        "ix_audit_chain_heads_tenant_id",
        "audit_chain_heads",
        ["tenant_id"],
        unique=False,
    )

    _backfill(op.get_bind())

    for column, type_ in (
        ("chain_key", sa.String(64)),
        ("seq", sa.BigInteger()),
        ("prev_hash", sa.String(64)),
        ("entry_hash", sa.String(64)),
    ):
        op.alter_column(
            "audit_events",
            column,
            existing_type=type_,
            nullable=False,
        )
    op.create_unique_constraint(
        "uq_audit_events_chain_key_seq",
        "audit_events",
        ["chain_key", "seq"],
    )


def downgrade() -> None:
    """Drop the chain columns, the uniqueness and the head table."""
    op.drop_constraint("uq_audit_events_chain_key_seq", "audit_events", type_="unique")
    op.drop_column("audit_events", "entry_hash")
    op.drop_column("audit_events", "prev_hash")
    op.drop_column("audit_events", "seq")
    op.drop_column("audit_events", "chain_key")
    op.drop_index("ix_audit_chain_heads_tenant_id", table_name="audit_chain_heads")
    op.drop_table("audit_chain_heads")

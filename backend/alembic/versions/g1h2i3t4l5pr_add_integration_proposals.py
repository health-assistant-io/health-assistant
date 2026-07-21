"""add integration_proposals table (HITL proposals for integrations)

Workstream G.1 of the integrations follow-ups pass
(plan: dev/plans/integrations-sdk-followups-2026-07-21.md).

Adds a new ``integration_proposals`` table for human-in-the-loop catalog
write proposals sourced from integrations. Providers opt in via
``BaseHealthProvider.supports_hitl_proposals`` and emit
``IntegrationProposalSpec`` objects through ``pull_hitl_proposals``; the
engine persists each as a PROPOSED row + fires an HITL notification. The
user resolves via the new
``/api/v1/integrations/instance/{id}/proposals/.../resolve`` endpoint,
which routes the (possibly-edited) payload through
``catalog_proposal_service.apply_proposal`` — the same write path F.3
auto-applies through.

Status machine mirrors the chat-side ``HitlTaskStatus`` enum: PROPOSED →
CONFIRMED / DISMISSED / FAILED. Re-resolve from a terminal state returns
409 (idempotent contract).

Dedup: a partial unique index on ``(integration_id, dedup_key)`` makes
re-propose idempotent. ``dedup_key`` = sha256 hex of canonical JSON of
``(proposal_type, proposed_payload)``. The engine lookup-then-inserts,
and the partial unique index is the last-resort race guard.

Revision ID: g1h2i3t4l5pr
Revises: e1x2a3m4i5n6
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "g1h2i3t4l5pr"
down_revision = "e1x2a3m4i5n6"
branch_labels = None
depends_on = None


_DEDUP_INDEX = "uq_integration_proposals_integration_dedup"


def upgrade() -> None:
    op.create_table('integration_proposals',
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("proposal_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "confirmed",
                "failed",
                "dismissed",
                name="integration_proposal_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "proposed_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "resolved_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["user_integrations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["fhir_patients.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("integration_proposals", schema=None) as batch_op:
        batch_op.create_index(
            "ix_integration_proposals_integration_id",
            ["integration_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_integration_proposals_proposal_type",
            ["proposal_type"],
            unique=False,
        )
        batch_op.create_index(
            "ix_integration_proposals_status", ["status"], unique=False
        )
        batch_op.create_index(
            "ix_integration_proposals_dedup_key",
            ["dedup_key"],
            unique=False,
        )
        batch_op.create_index(
            "ix_integration_proposals_tenant_id",
            ["tenant_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_integration_proposals_integration_status",
            ["integration_id", "status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_integration_proposals_tenant_status",
            ["tenant_id", "status"],
            unique=False,
        )

    # Partial unique index — only enforces uniqueness when dedup_key is
    # non-NULL. NULL keys (legacy / non-canonicalizable proposals) bypass.
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_DEDUP_INDEX}
        ON integration_proposals (integration_id, dedup_key)
        WHERE dedup_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX}")
    with op.batch_alter_table("integration_proposals", schema=None) as batch_op:
        batch_op.drop_index("ix_integration_proposals_tenant_status")
        batch_op.drop_index("ix_integration_proposals_integration_status")
        batch_op.drop_index("ix_integration_proposals_tenant_id")
        batch_op.drop_index("ix_integration_proposals_dedup_key")
        batch_op.drop_index("ix_integration_proposals_status")
        batch_op.drop_index("ix_integration_proposals_proposal_type")
        batch_op.drop_index("ix_integration_proposals_integration_id")
    op.drop_table("integration_proposals")
    op.execute("DROP TYPE IF EXISTS integration_proposal_status")

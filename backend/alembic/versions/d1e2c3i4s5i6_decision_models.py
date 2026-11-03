"""Decision models (plan 26 Phase 5 / ADR-0032): transport seam + PHI guard.

Adds two columns:

* ``ai_models.decision_transport`` (nullable VARCHAR(20)) — the per-model
  transport seam (``'systemone'`` today; NULL = not a decision model). The
  decide funnel's registry resolves transport → engine; callers never branch
  on the value (D2).
* ``ai_task_assignments.phi_allowed`` (NOT NULL, default FALSE) — health's
  D17 PHI scope guard: ``decision_*`` assignments are PHI-excluded by
  default; a PHI-adjacent dispatch runs on the slot only when the flag was
  explicitly set (ADR-0020 §6 sensitive-scope exclusion). Gap-fill and
  set-default never set it; only the explicit task-assignment surface can.

No data migration: no existing row can be a decision model (the capability
is explicit-only, D1) and every existing assignment stays PHI-excluded
(default FALSE is the conservative shape).
"""

import sqlalchemy as sa

from alembic import op

revision = "d1e2c3i4s5i6"
down_revision = "c1h2a3t4b5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_models",
        sa.Column("decision_transport", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "ai_task_assignments",
        sa.Column(
            "phi_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_task_assignments", "phi_allowed")
    op.drop_column("ai_models", "decision_transport")

"""AI task registry, DB-level assignment law, per-AI-call audit (plan 4).

Three shapes, one revision:

**``ai_task_defs`` — the task vocabulary as a seeded lookup.**
``ai_task_assignments.task_type`` (free-form string) becomes
``task_type_id`` → ``ai_task_defs.id``. The table is seeded at boot from the one
TaskDef registry (``app/ai/providers/task_defs.py``) — seeding is data, not DDL,
so a new AI task never needs a migration (constitution rule 7b). The 19 rows
below are the registry at the time of this revision; ids are deterministic
(uuid5 of ``ai_task_defs/<code>`` under the namespace
``uuid5(NAMESPACE_URL, "https://neuronection.com/ai/task-defs")``, i.e.
``task_defs.TASK_DEF_NAMESPACE``) so the seeder, this backfill and the
registry-parity test all agree. Assignment rows whose
legacy ``task_type`` is not in the registry (``medication_interaction``,
``anomaly_detection`` — engine tasks, not LLM tasks; plan 4 W4) are deleted:
they never drove an LLM call and there is no owner for them.

**The assignment law (ADR-0037 single-slot), enforced by the database.**
* rows are revoked, never deleted — ``revoked_at`` replaces ``is_active``
  (``UPDATE ... WHERE is_active = false`` first, so a revoked row stays revoked);
* ``priority`` is dropped outright (no app-side precedence left to drift);
* fallbacks live on the specialist row (``fallback_provider_id``/
  ``fallback_model_id`` columns), not in extra rows;
* one active assignment per scope key:
  ``UNIQUE NULLS NOT DISTINCT (tenant_id, user_id, task_type_id)
  WHERE revoked_at IS NULL``. ``NULLS NOT DISTINCT`` is load-bearing: system
  rows are ``(NULL, NULL, task)`` and tenant rows ``(tenant, NULL, task)``, and
  plain NULL-distinct uniqueness would let duplicates through. (PG15+. The
  COALESCE expression-index fallback — ``COALESCE(tenant_id, '000…'::uuid)`` —
  is the documented alternative for older engines; this repo's dev/prod/CI
  databases are PG16, so the native form is used. Capability defaults need no
  second index: each default IS a task (``default``/``ocr``/``transcription``/
  ``embed``/``decision``), so this same index is the ``(tenant, user,
  capability)`` law for them.)

**``ai_call_logs`` — one row per model call** (operational audit). ``route`` and
``status`` are native PG enums (closed behavioral vocabularies,
``AICallRoute``/``AICallStatus``); ``task_type_id`` is the same FK vocabulary;
``provider_id``/``model_id`` are ``SET NULL`` so the audit outlives config;
``meta`` is an open JSONB payload with a sibling ``meta_schema_version``.

Downgrade restores the previous shape: free-form ``task_type`` backfilled from
the lookup, ``is_active``/``priority`` back, the audit table and the lookup
dropped. The two dropped engine-task assignment rows are not resurrected — this
revision deliberately discarded them.

Revision ID: 0003_ai_task_registry
Revises: 0002_telemetry
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_ai_task_registry"
down_revision: str | Sequence[str] | None = "0002_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen registry snapshot (id, code, label_key) at this revision. The boot
#: seeder reconciles label/enabled changes; ids/codes are the contract.
_TASK_DEFS = (
    ("9d6ba4e4-7117-5204-98b0-6dd1eb60f0bf", "default", "settings.ai.task_default"),
    ("c1626080-b0f5-54b4-af8a-076b07b6e8b6", "ocr", "settings.ai.task_ocr"),
    ("38ccfde3-59c3-562f-8b7a-6e1c5ca673ac", "nlp", "settings.ai.task_nlp"),
    (
        "3e985d1e-fc3c-5ec5-99aa-e3daacd9e589",
        "fill_biomarker_form",
        "settings.ai.task_fill_biomarker_form",
    ),
    (
        "63aadbac-480a-59a5-9491-4a922b4f574d",
        "fill_medication_form",
        "settings.ai.task_fill_medication_form",
    ),
    (
        "ca88bf3d-989a-5bae-b3b7-a38b1f0e7c75",
        "magic_fill_examination",
        "settings.ai.task_magic_fill_examination",
    ),
    (
        "b13179ad-b159-5c6a-be0a-46eeb2a19311",
        "define_biomarker",
        "settings.ai.task_define_biomarker",
    ),
    (
        "cd9d9cb5-cf42-5e41-95f7-82fe82c5fa43",
        "define_medication",
        "settings.ai.task_define_medication",
    ),
    (
        "ce8f88c9-2172-5348-ba4b-6a06f0e75d4a",
        "define_anatomy_graph",
        "settings.ai.task_define_anatomy_graph",
    ),
    (
        "e229f749-38ee-5f9d-bc78-497d275660f2",
        "suggest_category_icon",
        "settings.ai.task_suggest_category_icon",
    ),
    (
        "7b9d0491-880e-5de3-88e1-5659c2219d68",
        "generate_category_icon",
        "settings.ai.task_generate_category_icon",
    ),
    ("5acf26db-579a-5d40-926d-6c36c9210be3", "chat", "settings.ai.task_chat"),
    ("a4755b5d-caa6-5bce-bfe1-94a90c92fcdb", "transcription", "settings.ai.task_transcription"),
    ("506cac1a-3825-52d0-a124-59438c147f78", "embed", "settings.ai.task_embed"),
    (
        "7aa930ab-f155-5f23-bc98-63ede18e6b10",
        "decision_tool_dispatch",
        "settings.ai.task_decision_tool_dispatch",
    ),
    (
        "fa078888-4935-5fb5-a0c8-244746290be8",
        "decision_boolean_gate",
        "settings.ai.task_decision_boolean_gate",
    ),
    ("7a652ffa-1ff9-5bb8-b509-76a248f0215b", "decision_choice", "settings.ai.task_decision_choice"),
    ("5ddf89c3-1745-5af8-bc02-aa563b314893", "decision_score", "settings.ai.task_decision_score"),
    ("1325b4cb-4744-5cf9-9ac0-076275311e4d", "decision", "settings.ai.task_decision"),
)
_CALL_ROUTE = sa.Enum(
    "chat",
    "decision",
    "embed",
    "stt",
    "ocr",
    "extract",
    name="aicallroute",
)
_CALL_STATUS = sa.Enum(
    "ok",
    "error",
    "timeout",
    "cancelled",
    name="aicallstatus",
)


def upgrade() -> None:
    # --- the seeded task vocabulary -------------------------------------
    op.create_table(
        "ai_task_defs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label_key", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_task_defs")),
        sa.UniqueConstraint("code", name=op.f("uq_ai_task_defs_code")),
    )
    with op.batch_alter_table("ai_task_defs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_task_defs_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ai_task_defs_updated_at"), ["updated_at"], unique=False
        )

    op.bulk_insert(
        sa.table(
            "ai_task_defs",
            sa.column("id", sa.UUID()),
            sa.column("code", sa.String()),
            sa.column("label_key", sa.String()),
            sa.column("enabled", sa.Boolean()),
        ),
        [
            {"id": task_id, "code": code, "label_key": label_key, "enabled": True}
            for task_id, code, label_key in _TASK_DEFS
        ],
    )

    # --- ai_task_assignments: task FK, fallbacks, revocation -------------
    op.add_column("ai_task_assignments", sa.Column("task_type_id", sa.UUID(), nullable=True))
    op.execute(
        "UPDATE ai_task_assignments AS a "
        "SET task_type_id = d.id FROM ai_task_defs AS d WHERE d.code = a.task_type"
    )
    op.execute("DELETE FROM ai_task_assignments WHERE task_type_id IS NULL")
    op.alter_column("ai_task_assignments", "task_type_id", nullable=False)
    op.create_index(
        op.f("ix_ai_task_assignments_task_type_id"),
        "ai_task_assignments",
        ["task_type_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_ai_task_assignments_task_type_id"),
        "ai_task_assignments",
        "ai_task_defs",
        ["task_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "ai_task_assignments", sa.Column("fallback_provider_id", sa.UUID(), nullable=True)
    )
    op.add_column("ai_task_assignments", sa.Column("fallback_model_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ai_task_assignments_fallback_provider_id"),
        "ai_task_assignments",
        "ai_providers",
        ["fallback_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_ai_task_assignments_fallback_model_id"),
        "ai_task_assignments",
        "ai_models",
        ["fallback_model_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "ai_task_assignments", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE ai_task_assignments SET revoked_at = now() WHERE is_active IS NOT TRUE")

    # Composite/dependent indexes go before their columns: dropping
    # task_type or is_active cascades them away implicitly.
    op.drop_index("ix_ai_task_assignments_tenant_task", table_name="ai_task_assignments")
    op.drop_index("ix_ai_task_assignments_priority", table_name="ai_task_assignments")
    op.drop_index("ix_ai_task_assignments_is_active", table_name="ai_task_assignments")
    op.drop_index("ix_ai_task_assignments_task_type", table_name="ai_task_assignments")
    op.drop_column("ai_task_assignments", "priority")
    op.drop_column("ai_task_assignments", "is_active")
    op.drop_column("ai_task_assignments", "task_type")

    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_ai_task_assignments_tenant_task",
            ["tenant_id", "task_type_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_revoked_at"), ["revoked_at"], unique=False
        )

    # The law: one ACTIVE row per (tenant, user, task). NULLS NOT DISTINCT is
    # what makes system/tenant scope (NULL user_id) unique too.
    op.create_index(
        "uq_ai_task_assignments_scope_task",
        "ai_task_assignments",
        ["tenant_id", "user_id", "task_type_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # --- the per-AI-call audit -------------------------------------------
    op.create_table(
        "ai_call_logs",
        sa.Column("task_type_id", sa.UUID(), nullable=False),
        sa.Column("route", _CALL_ROUTE, nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=True),
        sa.Column("model_id", sa.UUID(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", _CALL_STATUS, nullable=False),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("meta_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ai_models.id"],
            name=op.f("fk_ai_call_logs_model_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name=op.f("fk_ai_call_logs_provider_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_type_id"],
            ["ai_task_defs.id"],
            name=op.f("fk_ai_call_logs_task_type_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ai_call_logs_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_call_logs")),
    )
    with op.batch_alter_table("ai_call_logs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_ai_call_logs_tenant_started", ["tenant_id", "started_at"], unique=False
        )
        batch_op.create_index(
            "ix_ai_call_logs_tenant_task_started",
            ["tenant_id", "task_type_id", "started_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ai_call_logs_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ai_call_logs_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_call_logs_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Restore the previous shape (free-form task_type, is_active, priority)."""
    op.drop_index("ix_ai_call_logs_tenant_task_started", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_tenant_started", table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_user_id"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_tenant_id"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_patient_id"), table_name="ai_call_logs")
    op.drop_table("ai_call_logs")
    _CALL_STATUS.drop(op.get_bind(), checkfirst=True)
    _CALL_ROUTE.drop(op.get_bind(), checkfirst=True)

    op.drop_index("uq_ai_task_assignments_scope_task", table_name="ai_task_assignments")
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_revoked_at"))
        batch_op.drop_index("ix_ai_task_assignments_tenant_task")

    op.add_column(
        "ai_task_assignments", sa.Column("task_type", sa.String(length=50), nullable=True)
    )
    op.execute(
        "UPDATE ai_task_assignments AS a "
        "SET task_type = d.code FROM ai_task_defs AS d WHERE d.id = a.task_type_id"
    )
    op.execute("DELETE FROM ai_task_assignments WHERE task_type IS NULL")
    op.alter_column("ai_task_assignments", "task_type", nullable=False)
    op.create_index(
        op.f("ix_ai_task_assignments_task_type"),
        "ai_task_assignments",
        ["task_type"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_ai_task_assignments_task_type_id"),
        "ai_task_assignments",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_ai_task_assignments_task_type_id"), table_name="ai_task_assignments")
    op.drop_column("ai_task_assignments", "task_type_id")

    op.add_column("ai_task_assignments", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.execute("UPDATE ai_task_assignments SET is_active = (revoked_at IS NULL)")
    op.add_column(
        "ai_task_assignments", sa.Column("priority", sa.Integer(), nullable=True, default=0)
    )
    op.execute("UPDATE ai_task_assignments SET priority = 0")
    op.create_index(
        op.f("ix_ai_task_assignments_is_active"),
        "ai_task_assignments",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ix_ai_task_assignments_priority", "ai_task_assignments", ["priority"], unique=False
    )
    op.create_index(
        "ix_ai_task_assignments_tenant_task",
        "ai_task_assignments",
        ["tenant_id", "task_type", "is_active"],
        unique=False,
    )
    op.drop_column("ai_task_assignments", "revoked_at")

    op.drop_constraint(
        op.f("fk_ai_task_assignments_fallback_model_id"),
        "ai_task_assignments",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_ai_task_assignments_fallback_provider_id"),
        "ai_task_assignments",
        type_="foreignkey",
    )
    op.drop_column("ai_task_assignments", "fallback_model_id")
    op.drop_column("ai_task_assignments", "fallback_provider_id")

    op.drop_index(op.f("ix_ai_task_defs_updated_at"), table_name="ai_task_defs")
    op.drop_index(op.f("ix_ai_task_defs_created_at"), table_name="ai_task_defs")
    op.drop_table("ai_task_defs")

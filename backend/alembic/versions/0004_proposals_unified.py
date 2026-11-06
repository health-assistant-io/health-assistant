"""One HITL proposal store: proposals + seeded proposal_kinds (plan 5).

Three shapes, one revision:

**``proposal_kinds`` — the proposal-kind vocabulary as a seeded lookup.**
The contract (``the family proposal-kinds contract``, vendored at
``app/extensions/hitl-proposal-kinds.json``) is the source of truth; the rows
below are the contract at this revision, with deterministic ids
(``uuid5`` of ``proposal_kinds/<code>`` under the frozen namespace
``uuid5(NAMESPACE_URL, "https://neuronection.com/hitl/proposal-kinds")``).
Boot seeding reconciles tier/schema/resolver/card_variant — a new kind is a
contract entry plus a row, never a migration (constitution rule 7b). No CHECK
on the kind: contract + CHECK + code would be three sources of truth.

**``proposals`` — the one table.** ``integration_proposals`` is replaced
(create + copy + drop, so every constraint/index carries the new table name —
a literal ``ALTER TABLE RENAME`` would leave ``integration_proposals_*``
names behind, which the no-legacy rule forbids). The new shape:
``source`` (chat|extension|automation, CHECK-mirrored) + ``source_ref``
(chat_message_id / user_integration_id / job id) + ``ordinal`` (card order,
replacing the lost JSONB array order), ``kind_id`` FK → ``proposal_kinds``,
``status`` (the seven-state machine, CHECK-mirrored), ``payload`` +
``payload_schema_version``, ``final_payload``, tenant-unique ``dedup_key``,
``expires_at`` / ``resolved_at`` / ``applied_at``, ``resolution_trace`` and
``superseded_by``.

**``chat_messages.tasks`` is dropped with NO replacement column** (rule 11):
its cards move into ``proposals`` (source='chat', source_ref=message id,
ordinal = array index). Message→proposals is a query.

Data migration: extension proposals are resketched (status mapping
proposed→proposed, confirmed→applied, dismissed→rejected, failed→failed;
``resolved_payload`` → ``final_payload``; ``resolved_by``/``resolution_note``
→ ``resolution_trace``); chat cards are lifted out of the JSONB array (unknown
``task_type`` values are dropped — there is no owner for an unregistered kind).
Dedup keys are re-scoped from per-integration to per-tenant: the first row per
``(tenant_id, dedup_key)`` keeps the key, later collisions get NULL (the row
survives; only the automatic re-submission shortcut is lost).

Downgrade restores the previous DDL shape (``downgrade base`` cascade works):
``integration_proposals`` + its indexes/type are recreated and backfilled,
``chat_messages.tasks`` is re-added and rebuilt from the chat proposals
(ordered by ``ordinal``), then ``proposals``/``proposal_kinds`` are dropped.

Revision ID: 0004_proposals_unified
Revises: 0003_ai_task_registry
Create Date: 2026-10-21

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_proposals_unified"
down_revision: str | Sequence[str] | None = "0003_ai_task_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen contract snapshot (id, code, tier, schema_ref, resolver, card_variant)
#: at this revision. The boot seeder reconciles the rest; ids/codes are the
#: contract (``tests/test_hitl_proposal_kinds.py`` pins them).
_PROPOSAL_KINDS = (
    (
        "92eb5573-d6bd-54e4-960c-d5f5e63c57ff",
        "create_event",
        1,
        "app.extensions.sdk.proposal_schemas:CreateEventPayload",
        "create_event",
        "create_clinical_event",
    ),
    (
        "c01d26d8-6e18-5e5a-b7a4-28e0c6d9fc15",
        "create_examination",
        1,
        "app.extensions.sdk.proposal_schemas:CreateExaminationPayload",
        "create_examination",
        "create_examination",
    ),
    (
        "b5c31fdd-d310-5613-b737-5b18deaa3859",
        "add_medication",
        1,
        "app.extensions.sdk.proposal_schemas:AddMedicationPayload",
        "add_medication",
        "add_medication",
    ),
    (
        "fc11d6d9-55f5-5cef-a7d6-4e90a22ff263",
        "add_biomarker_to_examination",
        1,
        "app.extensions.sdk.proposal_schemas:AddBiomarkerPayload",
        "add_biomarker_to_examination",
        "add_biomarker_to_examination",
    ),
    (
        "c3e78254-2305-545f-b7c9-8060963fb694",
        "create_biomarker_definition",
        1,
        "app.extensions.sdk.proposal_schemas:CatalogBiomarkerPayload",
        "catalog_biomarker",
        "create_biomarker_definition",
    ),
    (
        "9fadfa49-bd61-5945-a224-2f668909ba12",
        "create_medication_definition",
        1,
        "app.extensions.sdk.proposal_schemas:CatalogMedicationPayload",
        "catalog_medication",
        "create_medication_definition",
    ),
    (
        "e7da2121-32f8-5b5a-9988-1b33627f6624",
        "create_concept",
        1,
        "app.extensions.sdk.proposal_schemas:CatalogConceptPayload",
        "catalog_concept",
        "create_concept",
    ),
    (
        "9d04b243-7174-5ce5-98b2-113f566ef7e3",
        "create_edge",
        1,
        "app.extensions.sdk.proposal_schemas:CatalogEdgePayload",
        "catalog_edge",
        "create_edge",
    ),
    (
        "3b8ab611-0ad5-5cf8-9124-a8a832e29cd5",
        "add_allergy",
        1,
        "app.extensions.sdk.proposal_schemas:ClientCommittedPayload",
        "client_committed",
        "add_allergy",
    ),
    (
        "0f2be06c-f6ec-51f6-a6b3-8b9cdf390d1b",
        "create_allergy_definition",
        1,
        "app.extensions.sdk.proposal_schemas:ClientCommittedPayload",
        "client_committed",
        "create_allergy_definition",
    ),
    (
        "63616b29-51ad-55c7-8ed2-3f9133c38423",
        "generate_anatomy_graph",
        1,
        "app.extensions.sdk.proposal_schemas:ClientCommittedPayload",
        "client_committed",
        "generate_anatomy_graph",
    ),
    (
        "9a1db232-c82c-5404-9f0d-65738ddc2a98",
        "ask_user",
        1,
        "app.extensions.sdk.proposal_schemas:AskUserPayload",
        "record_only",
        "ask_user",
    ),
)

_STATUS_VALUES = ("proposed", "approved", "rejected", "applied", "failed", "expired", "superseded")
_SOURCE_VALUES = ("chat", "extension", "automation")

_PROPOSAL_STATUS_SQL = "'" + "', '".join(_STATUS_VALUES) + "'"
_PROPOSAL_SOURCE_SQL = "'" + "', '".join(_SOURCE_VALUES) + "'"


def _create_proposal_kinds() -> None:
    op.create_table(
        "proposal_kinds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("schema_ref", sa.String(length=160), nullable=False),
        sa.Column("resolver", sa.String(length=64), nullable=False),
        sa.Column("card_variant", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.CheckConstraint("tier BETWEEN 1 AND 2", name=op.f("ck_proposal_kinds_tier_allowed")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_kinds")),
        sa.UniqueConstraint("code", name=op.f("uq_proposal_kinds_code")),
        sa.UniqueConstraint("card_variant", name=op.f("uq_proposal_kinds_card_variant")),
    )
    op.create_index(
        op.f("ix_proposal_kinds_created_at"), "proposal_kinds", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_kinds_updated_at"), "proposal_kinds", ["updated_at"], unique=False
    )

    kinds_table = sa.table(
        "proposal_kinds",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("tier", sa.SmallInteger()),
        sa.column("schema_ref", sa.String()),
        sa.column("resolver", sa.String()),
        sa.column("card_variant", sa.String()),
        sa.column("enabled", sa.Boolean()),
    )
    op.bulk_insert(
        kinds_table,
        [
            {
                "id": UUID(kind_id),
                "code": code,
                "tier": tier,
                "schema_ref": schema_ref,
                "resolver": resolver,
                "card_variant": card_variant,
                "enabled": True,
            }
            for kind_id, code, tier, schema_ref, resolver, card_variant in _PROPOSAL_KINDS
        ],
    )


def _create_proposals() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.UUID(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=True),
        sa.Column("kind_id", sa.UUID(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'proposed'")
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "payload_schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("final_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolution_trace",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("superseded_by", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            f"status IN ({_PROPOSAL_STATUS_SQL})",
            name=op.f("ck_proposals_proposal_status"),
        ),
        sa.CheckConstraint(
            f"source IN ({_PROPOSAL_SOURCE_SQL})",
            name=op.f("ck_proposals_proposal_source"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_proposals_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_proposals_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"],
            ["proposal_kinds.id"],
            name=op.f("fk_proposals_kind_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["proposals.id"],
            name=op.f("fk_proposals_superseded_by"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposals")),
    )
    for index_name, columns, extra in (
        ("ix_proposals_patient_id", ["patient_id"], {}),
        ("ix_proposals_source_ref", ["source_ref"], {}),
        ("ix_proposals_kind_id", ["kind_id"], {}),
        ("ix_proposals_status", ["status"], {}),
        ("ix_proposals_expires_at", ["expires_at"], {}),
        ("ix_proposals_tenant_id", ["tenant_id"], {}),
        ("ix_proposals_user_id", ["user_id"], {}),
        ("ix_proposals_created_at", ["created_at"], {}),
        ("ix_proposals_updated_at", ["updated_at"], {}),
        ("ix_proposals_source_ref_ordinal", ["source", "source_ref", "ordinal"], {}),
        ("ix_proposals_tenant_status", ["tenant_id", "status"], {}),
        ("ix_proposals_patient_status", ["patient_id", "status"], {}),
        (
            "uq_proposals_tenant_dedup",
            ["tenant_id", "dedup_key"],
            {"unique": True, "postgresql_where": "dedup_key IS NOT NULL"},
        ),
    ):
        op.create_index(
            index_name, "proposals", columns, unique=extra.pop("unique", False), **extra
        )


def _kind_lookup_sql() -> str:
    """A ``VALUES (code, id)`` fragment frozen for this revision."""
    values = ", ".join(f"('{code}', '{kind_id}'::uuid)" for kind_id, code, *_ in _PROPOSAL_KINDS)
    return f"SELECT * FROM (VALUES {values}) AS k(code, id)"


def _copy_extension_proposals() -> None:
    op.execute(
        f"""
        WITH ranked AS (
            SELECT ip.*,
                   row_number() OVER (
                       PARTITION BY ip.tenant_id, ip.dedup_key
                       ORDER BY ip.created_at, ip.id
                   ) AS rn
            FROM integration_proposals ip
        )
        INSERT INTO proposals (
            id, tenant_id, user_id, patient_id, source, source_ref, ordinal, kind_id,
            status, payload, payload_schema_version, final_payload, dedup_key,
            expires_at, resolved_at, applied_at, resolution_trace,
            created_by, updated_by, created_at, updated_at
        )
        SELECT ip.id, ip.tenant_id, ip.created_by, ip.patient_id, 'extension',
               ip.integration_id, NULL, k.id,
               CASE ip.status
                   WHEN 'confirmed' THEN 'applied'
                   WHEN 'dismissed' THEN 'rejected'
                   ELSE ip.status::text
               END,
               jsonb_build_object(
                   'title', ip.title,
                   'context', COALESCE(ip.context, '{{}}'::jsonb),
                   'proposed_payload', ip.proposed_payload
               ),
               1, ip.resolved_payload,
               CASE WHEN ip.rn = 1 THEN ip.dedup_key ELSE NULL END,
               ip.created_at + interval '14 days',
               CASE WHEN ip.status IN ('confirmed', 'failed', 'dismissed')
                    THEN ip.resolved_at END,
               CASE WHEN ip.status = 'confirmed' THEN ip.resolved_at END,
               CASE WHEN ip.status = 'proposed' THEN '{{}}'::jsonb
                    ELSE jsonb_strip_nulls(jsonb_build_object(
                        'resolved_by', ip.resolved_by::text,
                        'note', ip.resolution_note,
                        'at', ip.resolved_at,
                        'action', ip.status::text
                    )) END,
               ip.created_by, ip.updated_by, ip.created_at, ip.updated_at
        FROM ranked ip
        JOIN ({_kind_lookup_sql()}) AS k ON k.code = ip.proposal_type
        """
    )


def _copy_chat_cards() -> None:
    op.execute(
        """
        INSERT INTO proposals (
            id, tenant_id, user_id, patient_id, source, source_ref, ordinal, kind_id,
            status, payload, payload_schema_version, final_payload, dedup_key,
            expires_at, resolved_at, applied_at, resolution_trace,
            created_by, updated_by, created_at, updated_at
        )
        SELECT (t.task->>'proposal_id')::uuid,
               s.tenant_id, s.user_id,
               CASE WHEN pg_input_is_valid(t.task->'context'->>'patient_id', 'uuid')
                    THEN (t.task->'context'->>'patient_id')::uuid END,
               'chat', m.id, (t.ordinal - 1), k.id,
               CASE t.task->>'status'
                   WHEN 'confirmed' THEN 'applied'
                   WHEN 'dismissed' THEN 'rejected'
                   WHEN 'failed' THEN 'failed'
                   ELSE 'proposed'
               END,
               jsonb_build_object(
                   'title', t.task->>'title',
                   'context', COALESCE(t.task->'context', '{}'::jsonb),
                   'proposed_payload', COALESCE(t.task->'proposed_payload', '{}'::jsonb)
               ),
               1, t.task->'resolved'->'final_payload', NULL,
               m.created_at + interval '14 days',
               CASE WHEN t.task->>'status' IN ('confirmed', 'dismissed', 'failed')
                    THEN COALESCE((t.task->'resolved'->>'at')::timestamptz, m.created_at) END,
               CASE WHEN t.task->>'status' = 'confirmed'
                    THEN COALESCE((t.task->'resolved'->>'at')::timestamptz, m.created_at) END,
               CASE WHEN t.task->>'status' IN ('confirmed', 'dismissed', 'failed')
                    THEN jsonb_strip_nulls(jsonb_build_object(
                        'resolved_by', t.task->'resolved'->>'confirmed_by',
                        'result', t.task->'resolved'->'result',
                        'error', t.task->'resolved'->>'error',
                        'at', t.task->'resolved'->>'at',
                        'action', t.task->>'status'
                    ))
                    ELSE '{}'::jsonb END,
               s.user_id, s.user_id, m.created_at, m.created_at
        FROM chat_messages m
        JOIN chat_sessions s ON s.id = m.session_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(m.tasks) = 'array' THEN m.tasks ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS t(task, ordinal)
        JOIN proposal_kinds k ON k.card_variant = t.task->>'task_type'
        WHERE t.task ? 'proposal_id'
          AND pg_input_is_valid(t.task->>'proposal_id', 'uuid')
        ON CONFLICT (id) DO NOTHING
        """
    )


def upgrade() -> None:
    _create_proposal_kinds()
    _create_proposals()
    _copy_extension_proposals()
    _copy_chat_cards()

    op.drop_column("chat_messages", "tasks")
    op.drop_table("integration_proposals")
    op.execute("DROP TYPE IF EXISTS integration_proposal_status")


def downgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        """
        UPDATE chat_messages m
        SET tasks = sub.tasks
        FROM (
            SELECT p.source_ref AS message_id,
                   jsonb_agg(
                       jsonb_build_object(
                           'schema_version', p.payload_schema_version,
                           'proposal_id', p.id,
                           'task_type', k.card_variant,
                           'title', p.payload->>'title',
                           'status', CASE p.status
                               WHEN 'applied' THEN 'confirmed'
                               WHEN 'rejected' THEN 'dismissed'
                               WHEN 'expired' THEN 'dismissed'
                               WHEN 'superseded' THEN 'dismissed'
                               WHEN 'failed' THEN 'failed'
                               ELSE 'proposed'
                           END,
                           'proposed_payload',
                               COALESCE(p.payload->'proposed_payload', '{}'::jsonb),
                           'context', COALESCE(p.payload->'context', '{}'::jsonb),
                           'created_at', to_char(
                               p.created_at AT TIME ZONE 'UTC',
                               'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                           ),
                           'resolved', CASE WHEN p.status = 'proposed' THEN NULL
                               ELSE jsonb_strip_nulls(jsonb_build_object(
                                   'confirmed_by', p.resolution_trace->>'resolved_by',
                                   'final_payload', p.final_payload,
                                   'result', p.resolution_trace->'result',
                                   'error', p.resolution_trace->>'error',
                                   'at', p.resolution_trace->>'at'
                               )) END
                       )
                       ORDER BY p.ordinal NULLS LAST, p.created_at
                   ) AS tasks
            FROM proposals p
            JOIN proposal_kinds k ON k.id = p.kind_id
            WHERE p.source = 'chat' AND p.source_ref IS NOT NULL
            GROUP BY p.source_ref
        ) sub
        WHERE m.id = sub.message_id
        """
    )

    op.create_table(
        "integration_proposals",
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("proposal_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed", "confirmed", "failed", "dismissed", name="integration_proposal_status"
            ),
            nullable=False,
        ),
        sa.Column("proposed_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
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
            name=op.f("fk_integration_proposals_integration_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_integration_proposals_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_proposals_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_proposals")),
    )
    for index_name, columns, extra in (
        ("ix_integration_proposals_created_at", ["created_at"], {}),
        ("ix_integration_proposals_dedup_key", ["dedup_key"], {}),
        ("ix_integration_proposals_integration_id", ["integration_id"], {}),
        ("ix_integration_proposals_integration_status", ["integration_id", "status"], {}),
        ("ix_integration_proposals_proposal_type", ["proposal_type"], {}),
        ("ix_integration_proposals_status", ["status"], {}),
        ("ix_integration_proposals_tenant_id", ["tenant_id"], {}),
        ("ix_integration_proposals_tenant_status", ["tenant_id", "status"], {}),
        ("ix_integration_proposals_updated_at", ["updated_at"], {}),
        (
            "uq_integration_proposals_integration_dedup",
            ["integration_id", "dedup_key"],
            {"unique": True, "postgresql_where": "dedup_key IS NOT NULL"},
        ),
    ):
        op.create_index(
            index_name,
            "integration_proposals",
            columns,
            unique=extra.pop("unique", False),
            **extra,
        )

    op.execute(
        """
        INSERT INTO integration_proposals (
            id, integration_id, patient_id, proposal_type, title, status,
            proposed_payload, context, resolved_payload, resolved_by, resolved_at,
            resolution_note, dedup_key, tenant_id, created_by, updated_by,
            created_at, updated_at
        )
        SELECT p.id, p.source_ref, p.patient_id, k.code,
               COALESCE(p.payload->>'title', k.code),
               (CASE p.status
                   WHEN 'applied' THEN 'confirmed'
                   WHEN 'rejected' THEN 'dismissed'
                   WHEN 'expired' THEN 'dismissed'
                   WHEN 'superseded' THEN 'dismissed'
                   WHEN 'failed' THEN 'failed'
                   ELSE 'proposed'
               END)::integration_proposal_status,
               COALESCE(p.payload->'proposed_payload', '{}'::jsonb),
               COALESCE(p.payload->'context', '{}'::jsonb),
               p.final_payload,
               NULLIF(p.resolution_trace->>'resolved_by', '')::uuid,
               p.resolved_at,
               p.resolution_trace->>'note',
               p.dedup_key, p.tenant_id, p.created_by, p.updated_by,
               p.created_at, p.updated_at
        FROM proposals p
        JOIN proposal_kinds k ON k.id = p.kind_id
        WHERE p.source = 'extension' AND p.source_ref IS NOT NULL
        """
    )

    op.drop_table("proposals")
    op.drop_table("proposal_kinds")

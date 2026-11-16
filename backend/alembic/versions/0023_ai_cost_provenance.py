"""AI call cost provenance + tenant price overrides (plan 58 P2).

Revision ID: 0023_ai_cost_provenance
Revises: 0022_integration_principals
Create Date: 2026-11-11
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0023_ai_cost_provenance"
down_revision: str | Sequence[str] | None = "0022_integration_principals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"
_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"


def upgrade() -> None:
    with op.batch_alter_table("ai_call_logs") as batch:
        batch.add_column(sa.Column("provider_key", sa.String(length=40), nullable=True))
        batch.add_column(
            sa.Column(
                "usage_source",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(sa.Column("estimate_method", sa.String(length=40), nullable=True))
        batch.add_column(
            sa.Column(
                "price_source",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(sa.Column("price_version", sa.String(length=120), nullable=True))
        batch.alter_column(
            "cost_micros",
            existing_type=sa.BigInteger(),
            nullable=True,
            server_default=None,
        )
        batch.create_check_constraint(
            "usage_source_allowed",
            "usage_source IN ('provider', 'estimated', 'partial', 'unknown')",
        )
        batch.create_check_constraint(
            "price_source_allowed",
            "price_source IN ('user', 'builtin', 'local', 'legacy', 'unknown')",
        )

    # Old rows could not distinguish "no declared price" from a real free
    # call: the writer stored 0 for both. A zero row whose model still carries
    # its declared per-1k settings is recomputed as legacy history; every
    # other zero becomes unpriced, never a fabricated free call.
    op.execute(
        """
        UPDATE ai_call_logs AS l
           SET cost_micros = CASE
                   WHEN l.prompt_tokens IS NOT NULL
                    AND l.completion_tokens IS NOT NULL
                   THEN round(
                       l.prompt_tokens
                         * (m.settings ->> 'cost_per_1k_input_micros')::numeric / 1000
                       + l.completion_tokens
                         * (m.settings ->> 'cost_per_1k_output_micros')::numeric / 1000
                   )::bigint
                   ELSE NULL
               END,
               price_source = 'legacy',
               price_version = 'pre-contract',
               usage_source = CASE
                   WHEN l.prompt_tokens IS NOT NULL AND l.completion_tokens IS NOT NULL
                       THEN 'provider'
                   WHEN l.prompt_tokens IS NOT NULL OR l.completion_tokens IS NOT NULL
                       THEN 'partial'
                   ELSE 'unknown'
               END
          FROM ai_models AS m
         WHERE l.model_id = m.id
           AND l.cost_micros = 0
           AND m.settings ? 'cost_per_1k_input_micros'
           AND m.settings ? 'cost_per_1k_output_micros'
        """
    )
    op.execute(
        """
        UPDATE ai_call_logs
           SET cost_micros = NULL,
               price_source = 'unknown',
               usage_source = CASE
                   WHEN prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL
                       THEN 'provider'
                   WHEN prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL
                       THEN 'partial'
                   ELSE 'unknown'
               END
         WHERE cost_micros = 0
        """
    )
    op.execute(
        """
        UPDATE ai_call_logs
           SET price_source = 'legacy',
               price_version = 'pre-contract',
               usage_source = CASE
                   WHEN prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL
                       THEN 'provider'
                   WHEN prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL
                       THEN 'partial'
                   ELSE 'unknown'
               END
         WHERE cost_micros IS NOT NULL AND cost_micros <> 0
        """
    )

    op.create_table(
        "ai_price_overrides",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_key", sa.String(length=40), nullable=True),
        sa.Column("provider_ref", UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("match", sa.String(length=10), nullable=False, server_default="exact"),
        sa.Column("input_micros_per_million", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_micros_per_million", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("metering", sa.String(length=20), nullable=False, server_default="tokens"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "provider_key IS NULL OR provider_ref IS NULL",
            name="ck_ai_price_overrides_one_provider_binding",
        ),
        sa.CheckConstraint(
            "match IN ('exact', 'prefix')",
            name="ck_ai_price_overrides_match_allowed",
        ),
        sa.CheckConstraint(
            "metering IN ('tokens', 'input_only', 'output_only')",
            name="ck_ai_price_overrides_metering_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_ai_price_overrides_tenant_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_ref"],
            ["ai_providers.id"],
            name="fk_ai_price_overrides_provider_ref",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_price_overrides"),
    )
    op.create_index(
        "ix_ai_price_overrides_tenant_id",
        "ai_price_overrides",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_ai_price_overrides_scope",
        "ai_price_overrides",
        ["tenant_id", "provider_key", "provider_ref", "model", "match"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_ai_price_overrides_created_at",
        "ai_price_overrides",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_price_overrides_updated_at",
        "ai_price_overrides",
        ["updated_at"],
        unique=False,
    )
    op.execute("ALTER TABLE ai_price_overrides ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY rls_tenant ON ai_price_overrides "
        f"USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ai_price_overrides TO {APP_ROLE}")

    # Per-model declared settings were the pre-contract price store. A tenant
    # provider's exact pair becomes a tenant override bound by preset key (or
    # providerRef for a custom row); system-scope providers keep the legacy
    # fallback because an override needs a tenant.
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
            SELECT m.id, m.model_name, m.settings, p.id AS provider_id,
                   p.preset_key, p.tenant_id
              FROM ai_models AS m
              JOIN ai_providers AS p ON p.id = m.provider_id
             WHERE p.tenant_id IS NOT NULL
               AND m.settings IS NOT NULL
               AND m.settings ? 'cost_per_1k_input_micros'
               AND m.settings ? 'cost_per_1k_output_micros'
            """
            )
        )
        .mappings()
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        settings = row["settings"] or {}
        raw_in = settings.get("cost_per_1k_input_micros")
        raw_out = settings.get("cost_per_1k_output_micros")
        if isinstance(raw_in, bool) or isinstance(raw_out, bool):
            continue
        if not isinstance(raw_in, (int, float)) or not isinstance(raw_out, (int, float)):
            continue
        provider_key = row["preset_key"]
        provider_ref = None if provider_key else row["provider_id"]
        exists = bind.execute(
            sa.text(
                """
                SELECT 1 FROM ai_price_overrides
                 WHERE tenant_id = :tenant_id
                   AND model = :model
                   AND match = 'exact'
                   AND provider_key IS NOT DISTINCT FROM CAST(:provider_key AS varchar)
                   AND provider_ref IS NOT DISTINCT FROM CAST(:provider_ref AS uuid)
                """
            ),
            {
                "tenant_id": row["tenant_id"],
                "model": row["model_name"],
                "provider_key": provider_key,
                "provider_ref": provider_ref,
            },
        ).scalar()
        if not exists:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO ai_price_overrides
                        (id, tenant_id, provider_key, provider_ref, model, match,
                         input_micros_per_million, output_micros_per_million,
                         metering, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :provider_key, :provider_ref, :model, 'exact',
                         :input, :output, 'tokens', :created_at, :updated_at)
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": row["tenant_id"],
                    "provider_key": provider_key,
                    "provider_ref": provider_ref,
                    "model": row["model_name"],
                    "input": round(raw_in * 1000),
                    "output": round(raw_out * 1000),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE ai_models
                       SET settings = settings
                           - 'cost_per_1k_input_micros'
                           - 'cost_per_1k_output_micros'
                     WHERE id = :model_id
                    """
                ),
                {"model_id": row["id"]},
            )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_tenant ON ai_price_overrides")
    op.drop_index("ix_ai_price_overrides_updated_at", table_name="ai_price_overrides")
    op.drop_index("ix_ai_price_overrides_created_at", table_name="ai_price_overrides")
    op.drop_index("uq_ai_price_overrides_scope", table_name="ai_price_overrides")
    op.drop_index("ix_ai_price_overrides_tenant_id", table_name="ai_price_overrides")
    op.drop_table("ai_price_overrides")
    op.execute("UPDATE ai_call_logs SET cost_micros = 0 WHERE cost_micros IS NULL")
    with op.batch_alter_table("ai_call_logs") as batch:
        batch.alter_column(
            "cost_micros",
            existing_type=sa.BigInteger(),
            nullable=False,
            server_default="0",
        )
        batch.drop_column("price_version")
        batch.drop_column("price_source")
        batch.drop_column("estimate_method")
        batch.drop_column("usage_source")
        batch.drop_column("provider_key")

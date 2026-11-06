# ruff: noqa: E501 -- long immutable strings; reflow when touched
"""Baseline schema — the single root of the health-assistant migration chain.

Reproduces the ORM models exactly (constitution rule 1) as of the 2026-10-21
schema future-proofing program: 65 tables, the ``Base.metadata`` five-key
naming convention (``pk_``/``fk_``/``ck_``/``uq_``/``ix_``), sequential
revision ids from here on (``NNNN_slug``), and no legacy carve-outs.

This migration was generated with ``alembic revision --autogenerate`` against
the cleaned model metadata on an empty database, then hand-augmented with the
pieces autogenerate cannot infer:

  * ``CREATE EXTENSION`` for ``pgcrypto``, ``pg_trgm`` and (optionally)
    ``timescaledb``.
  * The trigram / FTS / JSONB GIN expression indexes used by catalog search.
  * The per-tenant ``COALESCE(tenant_id, sentinel)`` unique indexes.
  * The deferred ``tenants.owner_id`` → ``users.id`` FK (breaks the
    tenants⇄users creation cycle).
  * The ``telemetry_data`` TimescaleDB hypertable conversion and its
    compression policy (the retention policy rides with the ladder in
    ``0002_telemetry``).

The continuous-aggregate ladder (``telemetry_hourly``/``_daily``/``_monthly``,
realtime semantics, refresh + retention policies, 7-day chunk interval) is
**not** part of this revision: it is created exactly once by
``0002_telemetry`` (plan 3 §2.3 froze the ladder as a decision). Keeping the
CAGG shape out of the baseline removes the plan-2↔plan-3 circularity.

Enum types are created inline by ``sa.Enum(..., name=...)`` during table
creation. Catalog/seed data is loaded idempotently by ``SeedService`` at
application startup, so no data backfills live here.

Downgrade — sanctioned deviation
--------------------------------
``downgrade()`` restores an **empty** database, not the pre-squash 28-revision
shape. This is the owner-approved baseline-squash carve-out recorded in
``the family migration rules`` §3 (2026-10-21): the project had no users and the chain
was squashed at v0.8.0, so there is no prior shape to restore. Every revision
after this one restores its previous shape as usual.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_extension(name: str) -> None:
    """Create a Postgres extension idempotently."""
    op.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")


def _create_optional_extension(name: str) -> None:
    """Create an extension only when the server ships it.

    ``timescaledb`` is optional: the family standards/test databases on plain Postgres must
    still be able to reach head (the hypertable/CAGG DDL is guarded
    separately), so a missing extension is a skip, not a failure.
    """
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = :name"),
        {"name": name.split()[0]},
    ).scalar()
    if available:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")


def _has_timescaledb() -> bool:
    """Return True when the TimescaleDB extension is available on the server."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


# Expression indexes that autogenerate cannot infer. These power catalog
# hybrid search (trigram + FTS tsvector) and JSONB containment lookups.
# Definitions are copied verbatim from the pre-squash schema so the baseline
# reproduces them exactly.
_EXPRESSION_INDEXES = [
    # --- trigram (per-column GIN) -------------------------------------------
    "CREATE INDEX ix_allergy_catalog_name_trgm ON allergy_catalog USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_anatomy_structures_name_trgm ON anatomy_structures USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_anatomy_structures_slug_trgm ON anatomy_structures USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_biomarker_definitions_name_trgm ON biomarker_definitions USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_biomarker_definitions_slug_trgm ON biomarker_definitions USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_concepts_name_trgm ON concepts USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_concepts_slug_trgm ON concepts USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_concepts_trgm ON concepts USING GIN (((name)::text || ' ' || (slug)::text) gin_trgm_ops)",
    "CREATE INDEX ix_medication_catalog_name_trgm ON medication_catalog USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_vaccine_catalog_name_trgm ON vaccine_catalog USING GIN (name gin_trgm_ops)",
    # --- JSONB GIN ----------------------------------------------------------
    "CREATE INDEX ix_concepts_aliases_gin ON concepts USING GIN (aliases)",
    "CREATE INDEX ix_fhir_observations_component_gin ON fhir_observations USING GIN (component)",
    # --- FTS tsvector (simple tokenizer, COALESCE-guarded) ------------------
    "CREATE INDEX ix_allergy_catalog_fts ON allergy_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '')))",
    "CREATE INDEX ix_anatomy_structures_fts ON anatomy_structures USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(standard_code, '')))",
    "CREATE INDEX ix_biomarker_definitions_fts ON biomarker_definitions USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(info, '') || ' ' || COALESCE(code, '') || ' ' || COALESCE(aliases::text, '')))",
    "CREATE INDEX ix_concepts_fts ON concepts USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(code, '') || ' ' || COALESCE(aliases::text, '')))",
    "CREATE INDEX ix_medication_catalog_fts ON medication_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(indications, '') || ' ' || COALESCE(side_effects::text, '') || ' ' || COALESCE(contraindications, '')))",
    "CREATE INDEX ix_vaccine_catalog_fts ON vaccine_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(code, '')))",
    # NOTE: the fhir_observations / fhir_diagnostic_reports ``subject ->> 'reference'``
    # expression indexes are declared on the ORM models and therefore already
    # emitted by the autogenerate block above — not duplicated here.
]


# Per-tenant unique indexes: a plain/global unique on ``slug`` / ``mrn`` is
# replaced by ``(col, COALESCE(tenant_id, <sentinel>))`` so the same slug or
# MRN can coexist across tenants (and NULL-tenant/system rows share one
# namespace). SQLAlchemy cannot render the COALESCE form, so these are raw SQL.
_NULL_TENANT = "00000000-0000-0000-0000-000000000000"

_PER_TENANT_UNIQUES = [
    # (index_name, table, column)
    ("ix_concepts_slug_tenant", "concepts", "slug"),
    ("ix_biomarker_definitions_slug_tenant", "biomarker_definitions", "slug"),
    ("ix_anatomy_structures_slug_tenant", "anatomy_structures", "slug"),
    ("ix_clinical_event_types_slug_tenant", "clinical_event_types", "slug"),
    ("ix_fhir_patients_mrn_tenant", "fhir_patients", "mrn"),
]


def upgrade() -> None:
    """Upgrade schema."""
    # --- Extensions ------------------------------------------------------
    _create_extension("pgcrypto")
    _create_extension("pg_trgm")
    # TimescaleDB is optional; hypertable DDL is guarded separately below.
    _create_optional_extension("timescaledb CASCADE")

    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "anatomy_figures",
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("figure_key", sa.String(length=50), nullable=False),
        sa.Column("view_key", sa.String(length=50), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("source_image_path", sa.String(length=500), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anatomy_figures")),
    )
    with op.batch_alter_table("anatomy_figures", schema=None) as batch_op:
        batch_op.create_index("ix_anatomy_figure_group", ["figure_key", "view_key"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_anatomy_figures_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_anatomy_figures_figure_key"), ["figure_key"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_anatomy_figures_slug"), ["slug"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_anatomy_figures_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "biomarker_states",
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("system", sa.String(length=255), nullable=False),
        sa.Column("display", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biomarker_states")),
        sa.UniqueConstraint("code", "system", name=op.f("uq_biomarker_states_code_system")),
    )
    with op.batch_alter_table("biomarker_states", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_biomarker_states_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_biomarker_states_slug"), ["slug"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_biomarker_states_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "catalog_audit_log",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("catalog_type", sa.String(length=50), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("from_scope", sa.String(length=20), nullable=True),
        sa.Column("to_scope", sa.String(length=20), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_audit_log")),
    )
    with op.batch_alter_table("catalog_audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_catalog_audit_created_at", ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_catalog_audit_log_catalog_type"), ["catalog_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_catalog_audit_log_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_catalog_audit_log_item_id"), ["item_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_catalog_audit_log_operation"), ["operation"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_catalog_audit_log_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_catalog_audit_log_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_catalog_audit_log_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_catalog_audit_type_item", ["catalog_type", "item_id"], unique=False
        )

    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_instance_settings")),
    )
    op.create_table(
        "system_integrations",
        sa.Column("domain", sa.String(length=50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("global_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk_system_integrations")),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_settings")),
    )
    with op.batch_alter_table("system_settings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_system_settings_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_system_settings_key"), ["key"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_system_settings_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "telemetry_data",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", "timestamp", name=op.f("pk_telemetry_data")),
    )
    with op.batch_alter_table("telemetry_data", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_device_id"), ["device_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            "ix_telemetry_data_tenant_device_ts",
            ["tenant_id", "device_id", "timestamp"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_telemetry_data_tenant_slug_ts", ["tenant_id", "slug", "timestamp"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_timestamp"), ["timestamp"], unique=False
        )

    op.create_table(
        "tenants",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tenants_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_tenants_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_tenants_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tenants_slug"), ["slug"], unique=True)
        batch_op.create_index(batch_op.f("ix_tenants_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "units",
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "quantity_type",
            sa.Enum(
                "MASS_CONCENTRATION",
                "MOLAR_CONCENTRATION",
                "NUMBER_CONCENTRATION",
                "PERCENTAGE",
                "PRESSURE",
                "VOLUME",
                "MASS",
                "TIME",
                "RATIO",
                "TEMPERATURE",
                "OTHER",
                name="quantitytype",
            ),
            nullable=False,
        ),
        sa.Column("base_unit_id", sa.UUID(), nullable=True),
        sa.Column("conversion_multiplier", sa.Float(), nullable=False),
        sa.Column("dashboard_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "conversion_multiplier > 0", name=op.f("ck_units_positive_conversion_multiplier")
        ),
        sa.ForeignKeyConstraint(
            ["base_unit_id"], ["units.id"], name=op.f("fk_units_base_unit_id"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_units")),
    )
    with op.batch_alter_table("units", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_units_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_units_symbol"), ["symbol"], unique=True)
        batch_op.create_index(batch_op.f("ix_units_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "users",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=200), server_default="", nullable=False),
        sa.Column(
            "role", sa.Enum("SYSTEM_ADMIN", "ADMIN", "MANAGER", "USER", name="role"), nullable=False
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("oidc_issuer", sa.String(length=500), nullable=True),
        sa.Column("oidc_subject", sa.String(length=500), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mfa_secret_enc", sa.String(length=512), nullable=True),
        sa.Column("mfa_recovery_codes", sa.Text(), nullable=True),
        sa.Column("mfa_pending", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mfa_enforced", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_users_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint(
            "oidc_issuer", "oidc_subject", name=op.f("uq_users_oidc_issuer_oidc_subject")
        ),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)
        batch_op.create_index(batch_op.f("ix_users_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "ai_providers",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("SYSTEM", "TENANT", "USER", "ORGANIZATION", name="aiscope"),
            nullable=False,
        ),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("api_base", sa.String(length=500), nullable=False),
        sa.Column("api_key", sa.String(length=500), nullable=True),
        sa.Column("preset_key", sa.String(length=40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_local", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("company_website", sa.String(length=500), nullable=True),
        sa.Column("company_country", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ai_providers_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_providers")),
    )
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_providers_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ai_providers_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_providers_name"), ["name"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_providers_scope"), ["scope"], unique=False)
        batch_op.create_index(
            "ix_ai_providers_tenant_active", ["tenant_id", "is_active"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ai_providers_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ai_providers_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_ai_providers_user", ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_providers_user_id"), ["user_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("outcome", sa.String(length=20), server_default="ok", nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_audit_events_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_action"), ["action"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_audit_events_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_audit_events_outcome"), ["outcome"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_events_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_events_user_id"), ["user_id"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_label", sa.String(length=200), server_default="", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False)

    op.create_table(
        "concept_edges",
        sa.Column(
            "src_type",
            sa.Enum(
                "concept",
                "biomarker",
                "medication",
                "clinical_event_type",
                "allergy",
                "immunization",
                "observation",
                "doctor",
                "examination",
                "anatomy",
                "document",
                name="edgeendpointtype",
            ),
            nullable=False,
        ),
        sa.Column("src_id", sa.UUID(), nullable=False),
        sa.Column(
            "dst_type",
            sa.Enum(
                "concept",
                "biomarker",
                "medication",
                "clinical_event_type",
                "allergy",
                "immunization",
                "observation",
                "doctor",
                "examination",
                "anatomy",
                "document",
                name="edgeendpointtype",
            ),
            nullable=False,
        ),
        sa.Column("dst_id", sa.UUID(), nullable=False),
        sa.Column(
            "relation",
            sa.Enum(
                "MEMBER_OF",
                "HAS_SPECIALTY",
                "CLASSIFIED_AS",
                "EXAMINES",
                "IMAGES",
                "PERFORMS",
                "ORDERS",
                "LOCATED_IN",
                "PART_OF",
                "BRANCH_OF",
                "DRAINS_INTO",
                "ARTICULATES_WITH",
                "INNERVATED_BY",
                "SUPPLIED_BY",
                "CONTINUOUS_WITH",
                "AFFECTS",
                "TREATS",
                "INDICATES",
                "PREVENTS",
                "CONTRAINDICATES",
                "CORRELATES_WITH",
                "CAUSED_BY",
                "MONITORS",
                "RISK_OF",
                "SCREENS_FOR",
                name="conceptrelationtype",
            ),
            nullable=False,
        ),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "source",
            sa.Enum("seed", "integration", "ai", "manual", name="conceptprovenance"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("approved", "proposed", "rejected", name="edgeapprovalstatus"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_concept_edges_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_edges")),
    )
    with op.batch_alter_table("concept_edges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_concept_edges_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_concept_edges_dst", ["dst_type", "dst_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_dst_id"), ["dst_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_dst_type"), ["dst_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_relation"), ["relation"], unique=False)
        batch_op.create_index(
            "ix_concept_edges_relation_status", ["relation", "status"], unique=False
        )
        batch_op.create_index("ix_concept_edges_src", ["src_type", "src_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_src_id"), ["src_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_src_type"), ["src_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_concept_edges_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_concept_edges_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "concepts",
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "primary_kind",
            sa.Enum(
                "specialty",
                "examination_category",
                "event_category",
                "biomarker_class",
                "biomarker_panel",
                "anatomy_class",
                "vaccine_class",
                "medication_class",
                "document_category",
                "disease",
                "body_system",
                "procedure",
                "lifestyle",
                "factor",
                "symptom",
                "organ",
                name="conceptkind",
            ),
            nullable=True,
        ),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("coding_system", sa.String(length=50), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("icon", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column(
            "status", sa.Enum("draft", "active", "retired", name="conceptstatus"), nullable=False
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["concepts.id"], name=op.f("fk_concepts_parent_id"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_concepts_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concepts")),
    )
    with op.batch_alter_table("concepts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_concepts_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_concepts_deleted_at"), ["deleted_at"], unique=False)
        batch_op.create_index("ix_concepts_parent", ["parent_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concepts_parent_id"), ["parent_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_concepts_primary_kind"), ["primary_kind"], unique=False
        )
        batch_op.create_index(
            "ix_concepts_primary_kind_status", ["primary_kind", "status"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_concepts_scope"), ["scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_concepts_slug"), ["slug"], unique=False)
        batch_op.create_index(batch_op.f("ix_concepts_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_concepts_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "export_jobs",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "scope", sa.Enum("patient", "group", "system", name="exportscope"), nullable=False
        ),
        sa.Column(
            "export_type",
            sa.Enum("fhir_only", "full_backup", "catalog_only", name="exporttype"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", "PARTIAL", name="jobstatus"),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("patient_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("manifest_path", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("resource_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("smart_scope", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100", name=op.f("ck_export_jobs_progress_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_export_jobs_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_export_jobs_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_jobs")),
    )
    with op.batch_alter_table("export_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_export_jobs_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_export_jobs_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_export_jobs_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_export_jobs_user_id"), ["user_id"], unique=False)

    op.create_table(
        "fhir_organizations",
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("type", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "org_type",
            sa.Enum(
                "HOUSEHOLD",
                "CLINIC",
                "DEPARTMENT",
                "PROVIDER_GROUP",
                "HOSPITAL",
                "OTHER",
                name="organizationtype",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("alias", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("telecom", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("part_of_id", sa.UUID(), nullable=True),
        sa.Column("contact", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["part_of_id"],
            ["fhir_organizations.id"],
            name=op.f("fk_fhir_organizations_part_of_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_organizations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_organizations")),
    )
    with op.batch_alter_table("fhir_organizations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_organizations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_organizations_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_organizations_org_type"), ["org_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_organizations_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_organizations_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_organization_tenant_name", ["tenant_id", "name"], unique=False)

    op.create_table(
        "fhir_patients",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "gender", sa.Enum("MALE", "FEMALE", "OTHER", "UNKNOWN", name="gender"), nullable=False
        ),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("deceased_boolean", sa.Boolean(), nullable=True),
        sa.Column("deceased_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("telecom", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mrn", sa.String(), nullable=True),
        sa.Column("emergency_contact", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dashboard_layout", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extensions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mrn IS NULL OR mrn <> ''", name=op.f("ck_fhir_patients_mrn_not_empty")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_patients_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_fhir_patients_user_id"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_patients")),
    )
    with op.batch_alter_table("fhir_patients", schema=None) as batch_op:
        batch_op.create_index("ix_fhir_patients_birth_date", ["birth_date"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_patients_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_patients_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_patients_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_patients_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_patients_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_patient_tenant_mrn", ["tenant_id", "mrn"], unique=False)
        batch_op.create_index("ix_patient_tenant_name", ["tenant_id", "name"], unique=False)

    op.create_table(
        "fhir_provenance",
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recorded", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("activity", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("agent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entity", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_provenance_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_provenance")),
    )
    with op.batch_alter_table("fhir_provenance", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_provenance_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_provenance_recorded"), ["recorded"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_provenance_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_provenance_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "import_jobs",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", "PARTIAL", name="jobstatus"),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total_records", sa.Integer(), nullable=False),
        sa.Column("processed_records", sa.Integer(), nullable=False),
        sa.Column("failed_records", sa.Integer(), nullable=False),
        sa.Column("restore_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100", name=op.f("ck_import_jobs_progress_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_import_jobs_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_import_jobs_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    with op.batch_alter_table("import_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_import_jobs_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_import_jobs_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_import_jobs_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_import_jobs_user_id"), ["user_id"], unique=False)

    op.create_table(
        "laboratories",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("standard_rating", sa.Float(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_laboratories_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_laboratories")),
    )
    with op.batch_alter_table("laboratories", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_laboratories_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_laboratories_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_laboratories_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "mobile_push_targets",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("encryption_pubkey", sa.Text(), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_mobile_push_targets_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mobile_push_targets_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mobile_push_targets")),
        sa.UniqueConstraint(
            "user_id", "device_id", name=op.f("uq_mobile_push_targets_user_id_device_id")
        ),
    )
    with op.batch_alter_table("mobile_push_targets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mobile_push_targets_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mobile_push_targets_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mobile_push_targets_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            "ix_mobile_push_targets_user_active", ["user_id", "is_active"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mobile_push_targets_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "notification_subscriptions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=True),
        sa.Column("subscription_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_subscriptions_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_subscriptions_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_subscriptions")),
    )
    with op.batch_alter_table("notification_subscriptions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notification_subscriptions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_subscriptions_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_subscriptions_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_subscriptions_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("client_secret_hash", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("bound_patient_id", sa.UUID(), nullable=True),
        sa.Column("is_confidential", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_oauth_clients_created_by_user_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_oauth_clients_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_clients")),
    )
    with op.batch_alter_table("oauth_clients", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_oauth_clients_bound_patient_id"), ["bound_patient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_oauth_clients_client_id"), ["client_id"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_oauth_clients_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_oauth_clients_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_oauth_clients_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_oauth_clients_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "task_logs",
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_task_logs_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_logs")),
    )
    with op.batch_alter_table("task_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_task_logs_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_logs_level"), ["level"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_logs_resource_id"), ["resource_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_logs_task_id"), ["task_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_logs_task_name"), ["task_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_logs_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "ai_models",
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"text\"]'"),
            nullable=False,
        ),
        sa.Column("decision_transport", sa.String(length=20), nullable=True),
        sa.Column("chat_transport", sa.String(length=20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("is_local", sa.Boolean(), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint("max_tokens > 0", name=op.f("ck_ai_models_positive_max_tokens")),
        sa.CheckConstraint(
            "temperature BETWEEN 0 AND 2", name=op.f("ck_ai_models_temperature_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name=op.f("fk_ai_models_provider_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_models")),
    )
    with op.batch_alter_table("ai_models", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ai_models_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_models_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_models_name"), ["name"], unique=False)
        batch_op.create_index(
            "ix_ai_models_provider_active", ["provider_id", "is_active"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ai_models_provider_id"), ["provider_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_models_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "allergy_catalog",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "FOOD", "MEDICATION", "ENVIRONMENT", "BIOLOGIC", "OTHER", name="allergycategory"
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("typical_reactions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("class_concept_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["class_concept_id"],
            ["concepts.id"],
            name=op.f("fk_allergy_catalog_class_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_allergy_catalog_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_allergy_catalog")),
    )
    with op.batch_alter_table("allergy_catalog", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_allergy_catalog_class_concept_id"), ["class_concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_allergy_catalog_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_allergy_catalog_scope"), ["scope"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_allergy_catalog_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_allergy_catalog_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "anatomy_structures",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("class_concept_id", sa.UUID(), nullable=True),
        sa.Column(
            "standard_system",
            sa.Enum("LOINC", "SNOMED", "CUSTOM", name="codingsystem"),
            nullable=True,
        ),
        sa.Column("standard_code", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False),
        sa.Column("display", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["class_concept_id"],
            ["concepts.id"],
            name=op.f("fk_anatomy_structures_class_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_anatomy_structures_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anatomy_structures")),
    )
    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_anatomy_structures_class_concept_id"), ["class_concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_anatomy_structures_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_anatomy_structures_scope"), ["scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_anatomy_structures_slug"), ["slug"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_anatomy_structures_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_anatomy_structures_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_anatomy_tenant_slug", ["tenant_id", "slug"], unique=False)

    op.create_table(
        "biomarker_definitions",
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column(
            "coding_system",
            sa.Enum("LOINC", "SNOMED", "CUSTOM", name="codingsystem"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("class_concept_id", sa.UUID(), nullable=True),
        sa.Column("preferred_unit_id", sa.UUID(), nullable=True),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("info", sa.Text(), nullable=True),
        sa.Column("reference_range_min", sa.Float(), nullable=True),
        sa.Column("reference_range_max", sa.Float(), nullable=True),
        sa.Column("is_telemetry", sa.Boolean(), nullable=False),
        sa.Column(
            "value_type",
            sa.Enum("quantity", "state", name="biomarkervaluetype"),
            server_default="quantity",
            nullable=False,
        ),
        sa.Column(
            "supports_multi_state", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "is_telemetry = FALSE OR value_type != 'state'",
            name=op.f("ck_biomarker_definitions_state_not_telemetry"),
        ),
        sa.CheckConstraint(
            "value_type != 'state' OR preferred_unit_id IS NULL",
            name=op.f("ck_biomarker_definitions_state_no_unit"),
        ),
        sa.CheckConstraint(
            "reference_range_min IS NULL OR reference_range_max IS NULL OR reference_range_min <= reference_range_max",
            name=op.f("ck_biomarker_definitions_ref_range_order"),
        ),
        sa.ForeignKeyConstraint(
            ["class_concept_id"],
            ["concepts.id"],
            name=op.f("fk_biomarker_definitions_class_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["preferred_unit_id"],
            ["units.id"],
            name=op.f("fk_biomarker_definitions_preferred_unit_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_biomarker_definitions_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biomarker_definitions")),
    )
    with op.batch_alter_table("biomarker_definitions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_biomarker_definitions_class_concept_id"),
            ["class_concept_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_biomarker_definitions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_biomarker_definitions_scope"), ["scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_biomarker_definitions_slug"), ["slug"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_biomarker_definitions_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_biomarker_definitions_value_type"), ["value_type"], unique=False
        )

    op.create_table(
        "chat_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active_root_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_chat_sessions_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_chat_sessions_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_chat_sessions_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_active_root_id"), ["active_root_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_chat_sessions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_chat_sessions_user_id"), ["user_id"], unique=False)

    op.create_table(
        "clinical_event_types",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column("metadata_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("severity_scale", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("phases", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("milestones", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("default_duration_days", sa.Integer(), nullable=True),
        sa.Column(
            "schedule_kind",
            sa.Enum("state", "range", "recurring", "point", name="schedulekind"),
            server_default="state",
            nullable=False,
        ),
        sa.Column("category_concept_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["category_concept_id"],
            ["concepts.id"],
            name=op.f("fk_clinical_event_types_category_concept_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_clinical_event_types_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinical_event_types")),
    )
    with op.batch_alter_table("clinical_event_types", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_clinical_event_types_category_concept_id"),
            ["category_concept_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_types_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_types_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_types_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "concept_kind_tags",
        sa.Column("concept_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "specialty",
                "examination_category",
                "event_category",
                "biomarker_class",
                "biomarker_panel",
                "anatomy_class",
                "vaccine_class",
                "medication_class",
                "document_category",
                "disease",
                "body_system",
                "procedure",
                "lifestyle",
                "factor",
                "symptom",
                "organ",
                name="conceptkind",
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
            name=op.f("fk_concept_kind_tags_concept_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_concept_kind_tags")),
    )
    with op.batch_alter_table("concept_kind_tags", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_concept_kind_tags_concept_id"), ["concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_concept_kind_tags_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_concept_kind_tags_kind", ["kind"], unique=False)
        batch_op.create_index("ix_concept_kind_tags_unique", ["concept_id", "kind"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_concept_kind_tags_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "doctors",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("specialty_concept_id", sa.UUID(), nullable=True),
        sa.Column("license_number", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("telecom", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("office_number", sa.String(length=50), nullable=True),
        sa.Column("office_details", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["specialty_concept_id"],
            ["concepts.id"],
            name=op.f("fk_doctors_specialty_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_doctors_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_doctors_user_id"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doctors")),
    )
    with op.batch_alter_table("doctors", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_doctors_specialty_concept_id"), ["specialty_concept_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_doctors_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_doctors_user_id"), ["user_id"], unique=False)

    op.create_table(
        "fhir_diagnostic_reports",
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("category", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("effective_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued", sa.DateTime(timezone=True), nullable=True),
        sa.Column("performer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("conclusion", sa.String(), nullable=True),
        sa.Column("conclusion_code", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("presented_form", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_diagnostic_reports_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_diagnostic_reports_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_diagnostic_reports")),
    )
    with op.batch_alter_table("fhir_diagnostic_reports", schema=None) as batch_op:
        batch_op.create_index(
            "ix_diagnostic_report_tenant_date", ["tenant_id", "effective_datetime"], unique=False
        )
        batch_op.create_index(
            "ix_diagnostic_report_tenant_patient", ["tenant_id", "subject"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_diagnostic_reports_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_diagnostic_reports_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_diagnostic_reports_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            "ix_fhir_diagnostic_reports_subject_ref",
            [sa.literal_column("(subject->>'reference')")],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_diagnostic_reports_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_diagnostic_reports_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "medication_catalog",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("indications", sa.Text(), nullable=True),
        sa.Column("side_effects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contraindications", sa.Text(), nullable=True),
        sa.Column("dosage_info", sa.Text(), nullable=True),
        sa.Column("class_concept_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["class_concept_id"],
            ["concepts.id"],
            name=op.f("fk_medication_catalog_class_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_medication_catalog_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medication_catalog")),
    )
    with op.batch_alter_table("medication_catalog", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_medication_catalog_class_concept_id"), ["class_concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_medication_catalog_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_medication_catalog_scope"), ["scope"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_medication_catalog_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_medication_catalog_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "notification_triggers",
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column(
            "trigger_type",
            sa.Enum("TIME", "RECURRING", "EVENT", "THRESHOLD", name="triggertype"),
            nullable=False,
        ),
        sa.Column(
            "notification_type",
            sa.Enum(
                "MEDICATION_REMINDER",
                "EXAMINATION_REMINDER",
                "BIOMARKER_ALERT",
                "BIOMARKER_THRESHOLD",
                "OUT_OF_RANGE",
                "CALENDAR_EVENT",
                "AI_SUGGESTION",
                "HITL_TASK",
                "AGENT_RESULT",
                "INTEGRATION_EVENT",
                "SYNC_FAILURE",
                "SYSTEM_UPDATE",
                "SYSTEM_BROADCAST",
                "SYSTEM_ERROR",
                "CLINICAL_EVENT",
                "CUSTOM",
                name="notificationtype",
            ),
            nullable=False,
        ),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("last_triggered", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_trigger", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_notification_triggers_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_triggers_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_triggers")),
    )
    with op.batch_alter_table("notification_triggers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_next_trigger"), ["next_trigger"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_reference_id"), ["reference_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_triggers_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_trigger_next_run", ["next_trigger", "enabled"], unique=False)

    op.create_table(
        "patient_layouts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("layout_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cards_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_patient_layouts_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_patient_layouts_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_patient_layouts_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_layouts")),
    )
    with op.batch_alter_table("patient_layouts", schema=None) as batch_op:
        batch_op.create_index("ix_layout_user_patient", ["user_id", "patient_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_patient_layouts_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_layouts_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_patient_layouts_user_id"), ["user_id"], unique=False)

    op.create_table(
        "user_integrations",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "ACTIVE", "EXPIRED", "ERROR", name="integrationstatus"),
            nullable=True,
        ),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.String(length=1000), nullable=True),
        sa.Column("provider_account_id", sa.String(length=255), nullable=True),
        sa.Column("instance_name", sa.String(length=255), nullable=True),
        sa.Column("is_debug_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_user_integrations_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_user_integrations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_integrations_user_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_integrations")),
    )
    with op.batch_alter_table("user_integrations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_integrations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_integrations_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_integrations_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_user_integrations_user_id"), ["user_id"], unique=False)

    op.create_table(
        "vaccine_catalog",
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("coding_system", sa.String(length=50), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=True),
        sa.Column("target_diseases", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dose_schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contraindications", sa.Text(), nullable=True),
        sa.Column("side_effects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("class_concept_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "scope", sa.Enum("system", "tenant", "user", name="catalogscope"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["class_concept_id"],
            ["concepts.id"],
            name=op.f("fk_vaccine_catalog_class_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_vaccine_catalog_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vaccine_catalog")),
    )
    with op.batch_alter_table("vaccine_catalog", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_vaccine_catalog_class_concept_id"), ["class_concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_vaccine_catalog_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_vaccine_catalog_scope"), ["scope"], unique=False)
        batch_op.create_index(batch_op.f("ix_vaccine_catalog_slug"), ["slug"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_vaccine_catalog_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_vaccine_catalog_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "ai_task_assignments",
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("SYSTEM", "TENANT", "USER", "ORGANIZATION", name="aiscope"),
            nullable=False,
        ),
        sa.Column("provider_id", sa.UUID(), nullable=True),
        sa.Column("model_id", sa.UUID(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("phi_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ai_models.id"],
            name=op.f("fk_ai_task_assignments_model_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name=op.f("fk_ai_task_assignments_provider_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ai_task_assignments_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_task_assignments")),
    )
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_is_active"), ["is_active"], unique=False
        )
        batch_op.create_index("ix_ai_task_assignments_priority", ["priority"], unique=False)
        batch_op.create_index("ix_ai_task_assignments_scope", ["scope"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_task_type"), ["task_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_ai_task_assignments_tenant_task",
            ["tenant_id", "task_type", "is_active"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_ai_task_assignments_user", ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "biomarker_allowed_states",
        sa.Column("biomarker_id", sa.UUID(), nullable=False),
        sa.Column("state_id", sa.UUID(), nullable=False),
        sa.Column("is_normal", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["biomarker_id"],
            ["biomarker_definitions.id"],
            name=op.f("fk_biomarker_allowed_states_biomarker_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["state_id"],
            ["biomarker_states.id"],
            name=op.f("fk_biomarker_allowed_states_state_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biomarker_allowed_states")),
        sa.UniqueConstraint(
            "biomarker_id",
            "state_id",
            name=op.f("uq_biomarker_allowed_states_biomarker_id_state_id"),
        ),
    )
    with op.batch_alter_table("biomarker_allowed_states", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_biomarker_allowed_states_biomarker_id"), ["biomarker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_biomarker_allowed_states_state_id"), ["state_id"], unique=False
        )

    op.create_table(
        "biomarker_reference_ranges",
        sa.Column("biomarker_id", sa.UUID(), nullable=False),
        sa.Column(
            "sex", sa.Enum("MALE", "FEMALE", "OTHER", "UNKNOWN", name="gender"), nullable=True
        ),
        sa.Column("age_min", sa.Float(), nullable=True),
        sa.Column("age_max", sa.Float(), nullable=True),
        sa.Column("unit_id", sa.UUID(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("applies_to", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            "age_min IS NULL OR age_max IS NULL OR age_min <= age_max",
            name=op.f("ck_biomarker_reference_ranges_age_window"),
        ),
        sa.CheckConstraint(
            "low IS NULL OR high IS NULL OR low <= high",
            name=op.f("ck_biomarker_reference_ranges_low_le_high"),
        ),
        sa.ForeignKeyConstraint(
            ["biomarker_id"],
            ["biomarker_definitions.id"],
            name=op.f("fk_biomarker_reference_ranges_biomarker_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name=op.f("fk_biomarker_reference_ranges_unit_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biomarker_reference_ranges")),
    )
    with op.batch_alter_table("biomarker_reference_ranges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_biomarker_reference_ranges_biomarker_id"), ["biomarker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_biomarker_reference_ranges_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_biomarker_reference_ranges_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "chat_messages",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tasks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("active_child_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name=op.f("fk_chat_messages_session_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_messages_active_child_id"), ["active_child_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_chat_messages_parent_id"), ["parent_id"], unique=False)
        batch_op.create_index(
            "ix_chat_messages_session_created_at", ["session_id", "created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "clinical_events",
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("type_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "RESOLVED", "ON_HOLD", "UNKNOWN", name="clinicaleventstatus"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("onset_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "coding_system",
            sa.Enum("LOINC", "SNOMED", "CUSTOM", name="codingsystem"),
            nullable=True,
        ),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_clinical_events_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_clinical_events_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_clinical_events_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["clinical_event_types.id"],
            name=op.f("fk_clinical_events_type_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinical_events")),
    )
    with op.batch_alter_table("clinical_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_clinical_event_patient_type", ["patient_id", "type_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_external_id"), ["external_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_onset_date"), ["onset_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_resolved_date"), ["resolved_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_events_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_clinical_events_type_id"), ["type_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_clinical_events_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "examinations",
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("examination_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("patient_notes", sa.Text(), nullable=True),
        sa.Column("category_concept_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("auto_extract_metadata", sa.Boolean(), nullable=True),
        sa.Column("diagnoses", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("impressions", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(length=50), nullable=True),
        sa.Column("extraction_progress", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "extraction_progress BETWEEN 0 AND 100",
            name=op.f("ck_examinations_extraction_progress_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["category_concept_id"],
            ["concepts.id"],
            name=op.f("fk_examinations_category_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["fhir_organizations.id"],
            name=op.f("fk_examinations_organization_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_examinations_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_examinations_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_examinations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_examinations")),
    )
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.create_index("ix_exam_tenant_patient", ["tenant_id", "patient_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_examinations_category_concept_id"), ["category_concept_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_examination_date"), ["examination_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_external_id"), ["external_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_organization_id"), ["organization_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_examinations_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_examinations_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_examinations_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "fhir_allergy_intolerances",
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column(
            "clinical_status",
            sa.Enum("ACTIVE", "INACTIVE", "RESOLVED", name="allergyclinicalstatus"),
            nullable=False,
        ),
        sa.Column("verification_status", sa.String(length=50), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "FOOD", "MEDICATION", "ENVIRONMENT", "BIOLOGIC", "OTHER", name="allergycategory"
            ),
            nullable=True,
        ),
        sa.Column(
            "criticality",
            sa.Enum("LOW", "HIGH", "UNABLE_TO_ASSESS", name="allergycriticality"),
            nullable=True,
        ),
        sa.Column("code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("onset_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_occurrence", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reactions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_allergy_intolerances_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_fhir_allergy_intolerances_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_allergy_intolerances_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_allergy_intolerances")),
    )
    with op.batch_alter_table("fhir_allergy_intolerances", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_external_id"), ["external_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_allergy_intolerances_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "fhir_devices",
        sa.Column("identifier", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("device_name", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("type", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("model_number", sa.String(length=255), nullable=True),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("owner_integration_id", sa.UUID(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_fhir_devices_owner_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_devices_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_devices_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_devices")),
    )
    with op.batch_alter_table("fhir_devices", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_devices_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_devices_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_devices_owner_integration_id"),
            ["owner_integration_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_devices_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_devices_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_devices_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "integration_debug_logs",
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("level", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_integration_debug_logs_integration_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_debug_logs_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_debug_logs")),
    )
    with op.batch_alter_table("integration_debug_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_integration_debug_logs_integration_id"), ["integration_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_debug_logs_tenant_id"), ["tenant_id"], unique=False
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
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
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
    with op.batch_alter_table("integration_proposals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_dedup_key"), ["dedup_key"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_integration_id"), ["integration_id"], unique=False
        )
        batch_op.create_index(
            "ix_integration_proposals_integration_status",
            ["integration_id", "status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_proposal_type"), ["proposal_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_integration_proposals_tenant_status", ["tenant_id", "status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_proposals_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            "uq_integration_proposals_integration_dedup",
            ["integration_id", "dedup_key"],
            unique=True,
            postgresql_where="dedup_key IS NOT NULL",
        )

    op.create_table(
        "integration_sync_logs",
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("records_synced", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_integration_sync_logs_integration_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_integration_sync_logs_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_sync_logs")),
    )
    with op.batch_alter_table("integration_sync_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_integration_sync_logs_integration_id"), ["integration_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_integration_sync_logs_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "notification_rules",
        sa.Column(
            "rule_type",
            sa.Enum(
                "BIOMARKER_THRESHOLD",
                "OUT_OF_NORMAL_RANGE",
                "TREND_ANOMALY",
                "EVENT_LIFECYCLE",
                name="notificationruletype",
            ),
            nullable=False,
        ),
        sa.Column("biomarker_id", sa.UUID(), nullable=True),
        sa.Column(
            "operator",
            sa.Enum(">", "<", ">=", "<=", "==", "out_of_normal", name="comparisonoperator"),
            nullable=True,
        ),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column(
            "severity",
            sa.Enum("info", "warning", "critical", name="notificationseverity"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title_template", sa.String(length=255), nullable=True),
        sa.Column("body_template", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["biomarker_id"],
            ["biomarker_definitions.id"],
            name=op.f("fk_notification_rules_biomarker_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_notification_rules_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_rules_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_rules")),
    )
    with op.batch_alter_table("notification_rules", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notification_rule_lookup", ["tenant_id", "biomarker_id", "enabled"], unique=False
        )
        batch_op.create_index(
            "ix_notification_rule_patient", ["patient_id", "enabled"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_biomarker_id"), ["biomarker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_last_fired_at"), ["last_fired_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_rule_type"), ["rule_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_rules_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "organization_doctors",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctors.id"],
            name=op.f("fk_organization_doctors_doctor_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["fhir_organizations.id"],
            name=op.f("fk_organization_doctors_organization_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "doctor_id", name=op.f("pk_organization_doctors")
        ),
    )
    op.create_table(
        "clinical_event_occurrences",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("severity", sa.String(length=50), nullable=True),
        sa.Column("intensity", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("anatomy_id", sa.UUID(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["anatomy_id"],
            ["anatomy_structures.id"],
            name=op.f("fk_clinical_event_occurrences_anatomy_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["clinical_events.id"],
            name=op.f("fk_clinical_event_occurrences_event_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinical_event_occurrences")),
    )
    with op.batch_alter_table("clinical_event_occurrences", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_clinical_event_occurrences_anatomy_id"), ["anatomy_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_occurrences_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_occurrences_event_id"), ["event_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_occurrences_occurred_at"), ["occurred_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clinical_event_occurrences_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("category_concept_id", sa.UUID(), nullable=True),
        sa.Column("examination_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("entities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("include_in_extraction", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("is_edited", sa.Boolean(), nullable=False),
        sa.Column("practitioner_id", sa.UUID(), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name=op.f("ck_documents_progress_bounds")),
        sa.ForeignKeyConstraint(
            ["category_concept_id"],
            ["concepts.id"],
            name=op.f("fk_documents_category_concept_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_documents_examination_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_documents_owner_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["documents.id"], name=op.f("fk_documents_parent_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_documents_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["practitioner_id"],
            ["doctors.id"],
            name=op.f("fk_documents_practitioner_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_documents_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_documents_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.create_index("ix_doc_tenant_owner", ["tenant_id", "owner_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_documents_category_concept_id"), ["category_concept_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_documents_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_deleted_at"), ["deleted_at"], unique=False)
        batch_op.create_index(
            "ix_documents_entities_gin",
            [sa.literal_column("entities")],
            unique=False,
            postgresql_using="gin",
        )
        batch_op.create_index(
            batch_op.f("ix_documents_examination_id"), ["examination_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_documents_external_id"), ["external_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_filename"), ["filename"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_parent_id"), ["parent_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_documents_practitioner_id"), ["practitioner_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_documents_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_documents_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "event_anatomy_links",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("anatomy_id", sa.UUID(), nullable=False),
        sa.Column("relation_type", sa.String(length=50), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["anatomy_id"],
            ["anatomy_structures.id"],
            name=op.f("fk_event_anatomy_links_anatomy_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["clinical_events.id"],
            name=op.f("fk_event_anatomy_links_event_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_anatomy_links")),
    )
    with op.batch_alter_table("event_anatomy_links", schema=None) as batch_op:
        batch_op.create_index("ix_event_anatomy_link", ["event_id", "anatomy_id"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_event_anatomy_links_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_event_anatomy_links_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "event_examination_links",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("examination_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["clinical_events.id"],
            name=op.f("fk_event_examination_links_event_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_event_examination_links_examination_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_examination_links")),
    )
    with op.batch_alter_table("event_examination_links", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_event_examination_links_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_event_examination_links_event_id"), ["event_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_event_examination_links_examination_id"),
            ["examination_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_event_examination_links_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "examination_doctors",
        sa.Column("examination_id", sa.UUID(), nullable=False),
        sa.Column("doctor_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctors.id"],
            name=op.f("fk_examination_doctors_doctor_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_examination_doctors_examination_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("examination_id", "doctor_id", name=op.f("pk_examination_doctors")),
    )
    op.create_table(
        "fhir_communications",
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("category", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("priority", sa.String(length=50), nullable=True),
        sa.Column("subject_patient_id", sa.UUID(), nullable=True),
        sa.Column("encounter_id", sa.UUID(), nullable=True),
        sa.Column("topic", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sent", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sender", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recipient", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["encounter_id"],
            ["examinations.id"],
            name=op.f("fk_fhir_communications_encounter_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_communications_subject_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_communications_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_communications")),
    )
    with op.batch_alter_table("fhir_communications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_encounter_id"), ["encounter_id"], unique=False
        )
        batch_op.create_index("ix_fhir_communications_sent", ["sent"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_subject_patient_id"),
            ["subject_patient_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_communications_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "fhir_medications",
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("examination_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                "COMPLETED",
                "CANCELLED",
                "ENTERED_IN_ERROR",
                "INTENDED",
                "STOPPED",
                "ON_HOLD",
                "UNKNOWN",
                name="medicationstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "intent",
            sa.Enum("statement", "order", "plan", "proposal", name="medicationintent"),
            nullable=False,
        ),
        sa.Column("code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("dosage", sa.String(length=255), nullable=True),
        sa.Column("frequency", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_fhir_medications_examination_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_medications_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_fhir_medications_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_medications_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_medications")),
    )
    with op.batch_alter_table("fhir_medications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_examination_id"), ["examination_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_external_id"), ["external_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_medications_intent"), ["intent"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index("ix_fhir_medications_start_date", ["start_date"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_medications_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "patient_immunizations",
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("vaccine_catalog_id", sa.UUID(), nullable=True),
        sa.Column("examination_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("completed", "entered-in-error", "not-done", name="immunizationstatus"),
            nullable=False,
        ),
        sa.Column("vaccine_code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("administered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dose_number", sa.String(length=20), nullable=True),
        sa.Column("lot_number", sa.String(length=100), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_integration_id", sa.UUID(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_patient_immunizations_examination_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_patient_immunizations_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_integration_id"],
            ["user_integrations.id"],
            name=op.f("fk_patient_immunizations_source_integration_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_patient_immunizations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vaccine_catalog_id"],
            ["vaccine_catalog.id"],
            name=op.f("fk_patient_immunizations_vaccine_catalog_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_immunizations")),
    )
    with op.batch_alter_table("patient_immunizations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_patient_immunizations_administered_at", ["administered_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_examination_id"), ["examination_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_external_id"), ["external_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_source_integration_id"),
            ["source_integration_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_patient_immunizations_vaccine_catalog_id"),
            ["vaccine_catalog_id"],
            unique=False,
        )

    op.create_table(
        "fhir_observations",
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("examination_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("category", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("subject", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("effective_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value_quantity", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("value_string", sa.String(), nullable=True),
        sa.Column("value_codeableConcept", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_range", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("interpretation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("performer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("component", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("biomarker_id", sa.UUID(), nullable=True),
        sa.Column("lab_id", sa.UUID(), nullable=True),
        sa.Column("method", sa.String(length=255), nullable=True),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("raw_unit_id", sa.UUID(), nullable=True),
        sa.Column("normalized_value", sa.Float(), nullable=True),
        sa.Column("relative_score", sa.Float(), nullable=True),
        sa.Column("lab_reference_range", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["biomarker_id"],
            ["biomarker_definitions.id"],
            name=op.f("fk_fhir_observations_biomarker_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_fhir_observations_document_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["examination_id"],
            ["examinations.id"],
            name=op.f("fk_fhir_observations_examination_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lab_id"],
            ["laboratories.id"],
            name=op.f("fk_fhir_observations_lab_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_fhir_observations_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raw_unit_id"],
            ["units.id"],
            name=op.f("fk_fhir_observations_raw_unit_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fhir_observations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_observations")),
    )
    with op.batch_alter_table("fhir_observations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_biomarker_id"), ["biomarker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_deleted_at"), ["deleted_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_document_id"), ["document_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_examination_id"), ["examination_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_fhir_observations_lab_id"), ["lab_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_raw_unit_id"), ["raw_unit_id"], unique=False
        )
        batch_op.create_index(
            "ix_fhir_observations_subject_ref",
            [sa.literal_column("(subject->>'reference')")],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_fhir_observations_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index("ix_observation_tenant_code", ["tenant_id", "code"], unique=False)
        batch_op.create_index(
            "ix_observation_tenant_date", ["tenant_id", "effective_datetime"], unique=False
        )
        batch_op.create_index(
            "ix_observation_tenant_patient", ["tenant_id", "subject"], unique=False
        )

    op.create_table(
        "notifications",
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("trigger_id", sa.UUID(), nullable=True),
        sa.Column("communication_id", sa.UUID(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "SYSTEM",
                "INTEGRATION",
                "AGENT",
                "RULE",
                "CLINICAL",
                "SCHEDULED",
                name="notificationsource",
            ),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.Enum(
                "MEDICATION_REMINDER",
                "EXAMINATION_REMINDER",
                "BIOMARKER_ALERT",
                "BIOMARKER_THRESHOLD",
                "OUT_OF_RANGE",
                "CALENDAR_EVENT",
                "AI_SUGGESTION",
                "HITL_TASK",
                "AGENT_RESULT",
                "INTEGRATION_EVENT",
                "SYNC_FAILURE",
                "SYSTEM_UPDATE",
                "SYSTEM_BROADCAST",
                "SYSTEM_ERROR",
                "CLINICAL_EVENT",
                "CUSTOM",
                name="notificationtype",
            ),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "reminder",
                "alert",
                "hitl",
                "agent",
                "system",
                "integration",
                "clinical_event",
                name="notificationcategory",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("info", "warning", "critical", name="notificationseverity"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sender_user_id", sa.UUID(), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=True),
        sa.Column("dedup_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["communication_id"],
            ["fhir_communications.id"],
            name=op.f("fk_notifications_communication_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_notifications_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notifications_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["notification_triggers.id"],
            name=op.f("fk_notifications_trigger_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_category"), ["category"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_notifications_communication_id"), ["communication_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notifications_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notifications_dedup_expires_at"), ["dedup_expires_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_notifications_dedup_key"), ["dedup_key"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_notifications_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notifications_sender_user_id"), ["sender_user_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_notifications_source"), ["source"], unique=False)
        batch_op.create_index(batch_op.f("ix_notifications_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_notifications_type"), ["type"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_notifications_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "event_observation_links",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["clinical_events.id"],
            name=op.f("fk_event_observation_links_event_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["fhir_observations.id"],
            name=op.f("fk_event_observation_links_observation_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_observation_links")),
    )
    with op.batch_alter_table("event_observation_links", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_event_observation_links_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_event_observation_links_event_id"), ["event_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_event_observation_links_observation_id"),
            ["observation_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_event_observation_links_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "notification_deliveries",
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("IN_APP", "PUSH", "EMAIL", "SMS", name="notificationchannel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENT", "DELIVERED", "FAILED", name="notificationstatus"),
            nullable=False,
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name=op.f("fk_notification_deliveries_notification_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["notification_subscriptions.id"],
            name=op.f("fk_notification_deliveries_subscription_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_deliveries_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_deliveries_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
    )
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notification_deliveries_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_deliveries_notification_id"),
            ["notification_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_notification_deliveries_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_deliveries_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_deliveries_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            "ix_notification_delivery_lookup", ["notification_id", "channel"], unique=False
        )

    op.create_table(
        "notification_recipients",
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "recipient_kind",
            sa.Enum("USER", "PATIENT", "DOCTOR", "TENANT", "SYSTEM", name="recipientkind"),
            nullable=False,
        ),
        sa.Column("recipient_ref", sa.UUID(), nullable=True),
        sa.Column(
            "status", sa.Enum("unread", "read", "dismissed", name="recipientstatus"), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name=op.f("fk_notification_recipients_notification_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notification_recipients_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_recipients_user_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_recipients")),
    )
    with op.batch_alter_table("notification_recipients", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notification_recipient_tenant_status", ["tenant_id", "status"], unique=False
        )
        batch_op.create_index(
            "ix_notification_recipient_user_status", ["user_id", "status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_recipients_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_recipients_notification_id"),
            ["notification_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_notification_recipients_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_recipients_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_recipients_user_id"), ["user_id"], unique=False
        )

    # tenants.owner_id -> users.id, deferred: the FK would otherwise force
    # ``users`` to exist before ``tenants`` while ``users.tenant_id`` already
    # references ``tenants`` — a creation cycle.
    op.create_foreign_key(
        "fk_tenants_owner_id",
        "tenants",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- Expression indexes (trigram / FTS / JSONB GIN) ---------------------
    # Autogenerate cannot emit these because they are raw SQL expressions.
    for stmt in _EXPRESSION_INDEXES:
        op.execute(stmt)

    # --- Per-tenant unique indexes (slug / mrn) ----------------------------
    coalesce = f"COALESCE(tenant_id, '{_NULL_TENANT}'::uuid)"
    for index_name, table, column in _PER_TENANT_UNIQUES:
        op.execute(f"CREATE UNIQUE INDEX {index_name} ON {table} ({column}, {coalesce})")
    # concept_edges dedup: one edge per (endpoints, relation, tenant).
    op.execute(
        "CREATE UNIQUE INDEX ix_concept_edges_unique ON concept_edges "
        f"(src_type, src_id, dst_type, dst_id, relation, {coalesce})"
    )

    # --- TimescaleDB hypertable + policies ---------------------------------
    # Guarded so the migration still succeeds on plain-Postgres databases
    # where the extension is unavailable. The aggregate ladder lives in
    # ``0002_telemetry`` — the baseline carries no CAGG shape.
    if _has_timescaledb():
        op.execute(
            "SELECT create_hypertable('telemetry_data', 'timestamp', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute(
            "ALTER TABLE telemetry_data SET ("
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'tenant_id, device_id, slug', "
            "timescaledb.compress_orderby = 'timestamp DESC'"
            ")"
        )
        op.execute(
            "SELECT add_compression_policy('telemetry_data', "
            "INTERVAL '7 days', if_not_exists => true)"
        )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema — restores an empty database (sanctioned carve-out).

    The pre-squash chain shape is deliberately not reconstructed; see the
    module docstring. Extensions stay installed (they are database-level
    infrastructure, not application objects).
    """
    # Deferred tenants->users FK must go before either table is dropped.
    op.drop_constraint("fk_tenants_owner_id", "tenants", type_="foreignkey")

    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("notification_recipients", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_recipients_user_id"))
        batch_op.drop_index(batch_op.f("ix_notification_recipients_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notification_recipients_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notification_recipients_notification_id"))
        batch_op.drop_index(batch_op.f("ix_notification_recipients_created_at"))
        batch_op.drop_index("ix_notification_recipient_user_status")
        batch_op.drop_index("ix_notification_recipient_tenant_status")

    op.drop_table("notification_recipients")
    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_delivery_lookup")
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_user_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_notification_id"))
        batch_op.drop_index(batch_op.f("ix_notification_deliveries_created_at"))

    op.drop_table("notification_deliveries")
    with op.batch_alter_table("event_observation_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_observation_links_updated_at"))
        batch_op.drop_index(batch_op.f("ix_event_observation_links_observation_id"))
        batch_op.drop_index(batch_op.f("ix_event_observation_links_event_id"))
        batch_op.drop_index(batch_op.f("ix_event_observation_links_created_at"))

    op.drop_table("event_observation_links")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notifications_type"))
        batch_op.drop_index(batch_op.f("ix_notifications_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notifications_source"))
        batch_op.drop_index(batch_op.f("ix_notifications_sender_user_id"))
        batch_op.drop_index(batch_op.f("ix_notifications_patient_id"))
        batch_op.drop_index(batch_op.f("ix_notifications_dedup_key"))
        batch_op.drop_index(batch_op.f("ix_notifications_dedup_expires_at"))
        batch_op.drop_index(batch_op.f("ix_notifications_created_at"))
        batch_op.drop_index(batch_op.f("ix_notifications_communication_id"))
        batch_op.drop_index(batch_op.f("ix_notifications_category"))

    op.drop_table("notifications")
    with op.batch_alter_table("fhir_observations", schema=None) as batch_op:
        batch_op.drop_index("ix_observation_tenant_patient")
        batch_op.drop_index("ix_observation_tenant_date")
        batch_op.drop_index("ix_observation_tenant_code")
        batch_op.drop_index(batch_op.f("ix_fhir_observations_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_tenant_id"))
        batch_op.drop_index("ix_fhir_observations_subject_ref")
        batch_op.drop_index(batch_op.f("ix_fhir_observations_raw_unit_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_patient_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_lab_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_examination_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_document_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_created_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_observations_biomarker_id"))

    op.drop_table("fhir_observations")
    with op.batch_alter_table("patient_immunizations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_vaccine_catalog_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_updated_at"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_patient_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_external_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_examination_id"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_patient_immunizations_created_at"))
        batch_op.drop_index("ix_patient_immunizations_administered_at")

    op.drop_table("patient_immunizations")
    with op.batch_alter_table("fhir_medications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_medications_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_tenant_id"))
        batch_op.drop_index("ix_fhir_medications_start_date")
        batch_op.drop_index(batch_op.f("ix_fhir_medications_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_patient_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_intent"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_external_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_examination_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_medications_created_at"))

    op.drop_table("fhir_medications")
    with op.batch_alter_table("fhir_communications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_communications_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_communications_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_communications_subject_patient_id"))
        batch_op.drop_index("ix_fhir_communications_sent")
        batch_op.drop_index(batch_op.f("ix_fhir_communications_encounter_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_communications_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_communications_created_at"))

    op.drop_table("fhir_communications")
    op.drop_table("examination_doctors")
    with op.batch_alter_table("event_examination_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_examination_links_updated_at"))
        batch_op.drop_index(batch_op.f("ix_event_examination_links_examination_id"))
        batch_op.drop_index(batch_op.f("ix_event_examination_links_event_id"))
        batch_op.drop_index(batch_op.f("ix_event_examination_links_created_at"))

    op.drop_table("event_examination_links")
    with op.batch_alter_table("event_anatomy_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_anatomy_links_updated_at"))
        batch_op.drop_index(batch_op.f("ix_event_anatomy_links_created_at"))
        batch_op.drop_index("ix_event_anatomy_link")

    op.drop_table("event_anatomy_links")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_updated_at"))
        batch_op.drop_index(batch_op.f("ix_documents_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_documents_status"))
        batch_op.drop_index(batch_op.f("ix_documents_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_documents_practitioner_id"))
        batch_op.drop_index(batch_op.f("ix_documents_parent_id"))
        batch_op.drop_index(batch_op.f("ix_documents_owner_id"))
        batch_op.drop_index(batch_op.f("ix_documents_filename"))
        batch_op.drop_index(batch_op.f("ix_documents_external_id"))
        batch_op.drop_index(batch_op.f("ix_documents_examination_id"))
        batch_op.drop_index("ix_documents_entities_gin", postgresql_using="gin")
        batch_op.drop_index(batch_op.f("ix_documents_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_documents_created_at"))
        batch_op.drop_index(batch_op.f("ix_documents_category_concept_id"))
        batch_op.drop_index("ix_doc_tenant_owner")

    op.drop_table("documents")
    with op.batch_alter_table("clinical_event_occurrences", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_clinical_event_occurrences_updated_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_occurrences_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_occurrences_event_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_occurrences_created_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_occurrences_anatomy_id"))

    op.drop_table("clinical_event_occurrences")
    op.drop_table("organization_doctors")
    with op.batch_alter_table("notification_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_rules_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_rule_type"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_patient_id"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_last_fired_at"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_created_at"))
        batch_op.drop_index(batch_op.f("ix_notification_rules_biomarker_id"))
        batch_op.drop_index("ix_notification_rule_patient")
        batch_op.drop_index("ix_notification_rule_lookup")

    op.drop_table("notification_rules")
    with op.batch_alter_table("integration_sync_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integration_sync_logs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_integration_sync_logs_integration_id"))

    op.drop_table("integration_sync_logs")
    with op.batch_alter_table("integration_proposals", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_integration_proposals_integration_dedup", postgresql_where="dedup_key IS NOT NULL"
        )
        batch_op.drop_index(batch_op.f("ix_integration_proposals_updated_at"))
        batch_op.drop_index("ix_integration_proposals_tenant_status")
        batch_op.drop_index(batch_op.f("ix_integration_proposals_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_integration_proposals_status"))
        batch_op.drop_index(batch_op.f("ix_integration_proposals_proposal_type"))
        batch_op.drop_index("ix_integration_proposals_integration_status")
        batch_op.drop_index(batch_op.f("ix_integration_proposals_integration_id"))
        batch_op.drop_index(batch_op.f("ix_integration_proposals_dedup_key"))
        batch_op.drop_index(batch_op.f("ix_integration_proposals_created_at"))

    op.drop_table("integration_proposals")
    with op.batch_alter_table("integration_debug_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_integration_debug_logs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_integration_debug_logs_integration_id"))

    op.drop_table("integration_debug_logs")
    with op.batch_alter_table("fhir_devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_devices_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_devices_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_devices_patient_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_devices_owner_integration_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_devices_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_devices_created_at"))

    op.drop_table("fhir_devices")
    with op.batch_alter_table("fhir_allergy_intolerances", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_patient_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_external_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_allergy_intolerances_created_at"))

    op.drop_table("fhir_allergy_intolerances")
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_examinations_updated_at"))
        batch_op.drop_index(batch_op.f("ix_examinations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_examinations_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_examinations_organization_id"))
        batch_op.drop_index(batch_op.f("ix_examinations_external_id"))
        batch_op.drop_index(batch_op.f("ix_examinations_examination_date"))
        batch_op.drop_index(batch_op.f("ix_examinations_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_examinations_created_at"))
        batch_op.drop_index(batch_op.f("ix_examinations_category_concept_id"))
        batch_op.drop_index("ix_exam_tenant_patient")

    op.drop_table("examinations")
    with op.batch_alter_table("clinical_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_clinical_events_updated_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_type_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_source_integration_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_resolved_date"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_patient_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_onset_date"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_external_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_events_created_at"))
        batch_op.drop_index("ix_clinical_event_patient_type")

    op.drop_table("clinical_events")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_messages_updated_at"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_session_id"))
        batch_op.drop_index("ix_chat_messages_session_created_at")
        batch_op.drop_index(batch_op.f("ix_chat_messages_parent_id"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_created_at"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_active_child_id"))

    op.drop_table("chat_messages")
    with op.batch_alter_table("biomarker_reference_ranges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_biomarker_reference_ranges_updated_at"))
        batch_op.drop_index(batch_op.f("ix_biomarker_reference_ranges_created_at"))
        batch_op.drop_index(batch_op.f("ix_biomarker_reference_ranges_biomarker_id"))

    op.drop_table("biomarker_reference_ranges")
    with op.batch_alter_table("biomarker_allowed_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_biomarker_allowed_states_state_id"))
        batch_op.drop_index(batch_op.f("ix_biomarker_allowed_states_biomarker_id"))

    op.drop_table("biomarker_allowed_states")
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_user_id"))
        batch_op.drop_index("ix_ai_task_assignments_user")
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_updated_at"))
        batch_op.drop_index("ix_ai_task_assignments_tenant_task")
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_task_type"))
        batch_op.drop_index("ix_ai_task_assignments_scope")
        batch_op.drop_index("ix_ai_task_assignments_priority")
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_is_active"))
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_created_at"))

    op.drop_table("ai_task_assignments")
    with op.batch_alter_table("vaccine_catalog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_updated_at"))
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_slug"))
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_scope"))
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_created_at"))
        batch_op.drop_index(batch_op.f("ix_vaccine_catalog_class_concept_id"))

    op.drop_table("vaccine_catalog")
    with op.batch_alter_table("user_integrations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_integrations_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_integrations_updated_at"))
        batch_op.drop_index(batch_op.f("ix_user_integrations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_user_integrations_created_at"))

    op.drop_table("user_integrations")
    with op.batch_alter_table("patient_layouts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_patient_layouts_user_id"))
        batch_op.drop_index(batch_op.f("ix_patient_layouts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_patient_layouts_patient_id"))
        batch_op.drop_index("ix_layout_user_patient")

    op.drop_table("patient_layouts")
    with op.batch_alter_table("notification_triggers", schema=None) as batch_op:
        batch_op.drop_index("ix_trigger_next_run")
        batch_op.drop_index(batch_op.f("ix_notification_triggers_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notification_triggers_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notification_triggers_reference_id"))
        batch_op.drop_index(batch_op.f("ix_notification_triggers_patient_id"))
        batch_op.drop_index(batch_op.f("ix_notification_triggers_next_trigger"))
        batch_op.drop_index(batch_op.f("ix_notification_triggers_created_at"))

    op.drop_table("notification_triggers")
    with op.batch_alter_table("medication_catalog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_medication_catalog_updated_at"))
        batch_op.drop_index(batch_op.f("ix_medication_catalog_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_medication_catalog_scope"))
        batch_op.drop_index(batch_op.f("ix_medication_catalog_created_at"))
        batch_op.drop_index(batch_op.f("ix_medication_catalog_class_concept_id"))

    op.drop_table("medication_catalog")
    with op.batch_alter_table("fhir_diagnostic_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_diagnostic_reports_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_diagnostic_reports_tenant_id"))
        batch_op.drop_index("ix_fhir_diagnostic_reports_subject_ref")
        batch_op.drop_index(batch_op.f("ix_fhir_diagnostic_reports_patient_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_diagnostic_reports_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_diagnostic_reports_created_at"))
        batch_op.drop_index("ix_diagnostic_report_tenant_patient")
        batch_op.drop_index("ix_diagnostic_report_tenant_date")

    op.drop_table("fhir_diagnostic_reports")
    with op.batch_alter_table("doctors", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_doctors_user_id"))
        batch_op.drop_index(batch_op.f("ix_doctors_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_doctors_specialty_concept_id"))

    op.drop_table("doctors")
    with op.batch_alter_table("concept_kind_tags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_concept_kind_tags_updated_at"))
        batch_op.drop_index("ix_concept_kind_tags_unique")
        batch_op.drop_index("ix_concept_kind_tags_kind")
        batch_op.drop_index(batch_op.f("ix_concept_kind_tags_created_at"))
        batch_op.drop_index(batch_op.f("ix_concept_kind_tags_concept_id"))

    op.drop_table("concept_kind_tags")
    with op.batch_alter_table("clinical_event_types", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_clinical_event_types_updated_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_types_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_types_created_at"))
        batch_op.drop_index(batch_op.f("ix_clinical_event_types_category_concept_id"))

    op.drop_table("clinical_event_types")
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_updated_at"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_patient_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_created_at"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_active_root_id"))

    op.drop_table("chat_sessions")
    with op.batch_alter_table("biomarker_definitions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_value_type"))
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_updated_at"))
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_slug"))
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_scope"))
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_created_at"))
        batch_op.drop_index(batch_op.f("ix_biomarker_definitions_class_concept_id"))

    op.drop_table("biomarker_definitions")
    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.drop_index("ix_anatomy_tenant_slug")
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_updated_at"))
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_slug"))
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_scope"))
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_created_at"))
        batch_op.drop_index(batch_op.f("ix_anatomy_structures_class_concept_id"))

    op.drop_table("anatomy_structures")
    with op.batch_alter_table("allergy_catalog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_allergy_catalog_updated_at"))
        batch_op.drop_index(batch_op.f("ix_allergy_catalog_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_allergy_catalog_scope"))
        batch_op.drop_index(batch_op.f("ix_allergy_catalog_created_at"))
        batch_op.drop_index(batch_op.f("ix_allergy_catalog_class_concept_id"))

    op.drop_table("allergy_catalog")
    with op.batch_alter_table("ai_models", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_models_updated_at"))
        batch_op.drop_index(batch_op.f("ix_ai_models_provider_id"))
        batch_op.drop_index("ix_ai_models_provider_active")
        batch_op.drop_index(batch_op.f("ix_ai_models_name"))
        batch_op.drop_index(batch_op.f("ix_ai_models_is_active"))
        batch_op.drop_index(batch_op.f("ix_ai_models_created_at"))

    op.drop_table("ai_models")
    with op.batch_alter_table("task_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_logs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_task_logs_task_name"))
        batch_op.drop_index(batch_op.f("ix_task_logs_task_id"))
        batch_op.drop_index(batch_op.f("ix_task_logs_resource_id"))
        batch_op.drop_index(batch_op.f("ix_task_logs_level"))
        batch_op.drop_index(batch_op.f("ix_task_logs_created_at"))

    op.drop_table("task_logs")
    with op.batch_alter_table("oauth_clients", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_oauth_clients_updated_at"))
        batch_op.drop_index(batch_op.f("ix_oauth_clients_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_oauth_clients_is_active"))
        batch_op.drop_index(batch_op.f("ix_oauth_clients_created_at"))
        batch_op.drop_index(batch_op.f("ix_oauth_clients_client_id"))
        batch_op.drop_index(batch_op.f("ix_oauth_clients_bound_patient_id"))

    op.drop_table("oauth_clients")
    with op.batch_alter_table("notification_subscriptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_subscriptions_user_id"))
        batch_op.drop_index(batch_op.f("ix_notification_subscriptions_updated_at"))
        batch_op.drop_index(batch_op.f("ix_notification_subscriptions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_notification_subscriptions_created_at"))

    op.drop_table("notification_subscriptions")
    with op.batch_alter_table("mobile_push_targets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_mobile_push_targets_user_id"))
        batch_op.drop_index("ix_mobile_push_targets_user_active")
        batch_op.drop_index(batch_op.f("ix_mobile_push_targets_updated_at"))
        batch_op.drop_index(batch_op.f("ix_mobile_push_targets_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_mobile_push_targets_created_at"))

    op.drop_table("mobile_push_targets")
    with op.batch_alter_table("laboratories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_laboratories_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_laboratories_updated_at"))
        batch_op.drop_index(batch_op.f("ix_laboratories_created_at"))

    op.drop_table("laboratories")
    with op.batch_alter_table("import_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_jobs_user_id"))
        batch_op.drop_index(batch_op.f("ix_import_jobs_updated_at"))
        batch_op.drop_index(batch_op.f("ix_import_jobs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_import_jobs_created_at"))

    op.drop_table("import_jobs")
    with op.batch_alter_table("fhir_provenance", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fhir_provenance_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_provenance_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_provenance_recorded"))
        batch_op.drop_index(batch_op.f("ix_fhir_provenance_created_at"))

    op.drop_table("fhir_provenance")
    with op.batch_alter_table("fhir_patients", schema=None) as batch_op:
        batch_op.drop_index("ix_patient_tenant_name")
        batch_op.drop_index("ix_patient_tenant_mrn")
        batch_op.drop_index(batch_op.f("ix_fhir_patients_user_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_patients_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_patients_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_patients_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_patients_created_at"))
        batch_op.drop_index("ix_fhir_patients_birth_date")

    op.drop_table("fhir_patients")
    with op.batch_alter_table("fhir_organizations", schema=None) as batch_op:
        batch_op.drop_index("ix_organization_tenant_name")
        batch_op.drop_index(batch_op.f("ix_fhir_organizations_updated_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_organizations_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_fhir_organizations_org_type"))
        batch_op.drop_index(batch_op.f("ix_fhir_organizations_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_fhir_organizations_created_at"))

    op.drop_table("fhir_organizations")
    with op.batch_alter_table("export_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_export_jobs_user_id"))
        batch_op.drop_index(batch_op.f("ix_export_jobs_updated_at"))
        batch_op.drop_index(batch_op.f("ix_export_jobs_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_export_jobs_created_at"))

    op.drop_table("export_jobs")
    with op.batch_alter_table("concepts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_concepts_updated_at"))
        batch_op.drop_index(batch_op.f("ix_concepts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_concepts_slug"))
        batch_op.drop_index(batch_op.f("ix_concepts_scope"))
        batch_op.drop_index("ix_concepts_primary_kind_status")
        batch_op.drop_index(batch_op.f("ix_concepts_primary_kind"))
        batch_op.drop_index(batch_op.f("ix_concepts_parent_id"))
        batch_op.drop_index("ix_concepts_parent")
        batch_op.drop_index(batch_op.f("ix_concepts_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_concepts_created_at"))

    op.drop_table("concepts")
    with op.batch_alter_table("concept_edges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_concept_edges_updated_at"))
        batch_op.drop_index(batch_op.f("ix_concept_edges_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_concept_edges_src_type"))
        batch_op.drop_index(batch_op.f("ix_concept_edges_src_id"))
        batch_op.drop_index("ix_concept_edges_src")
        batch_op.drop_index("ix_concept_edges_relation_status")
        batch_op.drop_index(batch_op.f("ix_concept_edges_relation"))
        batch_op.drop_index(batch_op.f("ix_concept_edges_dst_type"))
        batch_op.drop_index(batch_op.f("ix_concept_edges_dst_id"))
        batch_op.drop_index("ix_concept_edges_dst")
        batch_op.drop_index(batch_op.f("ix_concept_edges_created_at"))

    op.drop_table("concept_edges")
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))

    op.drop_table("auth_sessions")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_user_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_outcome"))
        batch_op.drop_index(batch_op.f("ix_audit_events_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_events_action"))

    op.drop_table("audit_events")
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_providers_user_id"))
        batch_op.drop_index("ix_ai_providers_user")
        batch_op.drop_index(batch_op.f("ix_ai_providers_updated_at"))
        batch_op.drop_index(batch_op.f("ix_ai_providers_tenant_id"))
        batch_op.drop_index("ix_ai_providers_tenant_active")
        batch_op.drop_index(batch_op.f("ix_ai_providers_scope"))
        batch_op.drop_index(batch_op.f("ix_ai_providers_name"))
        batch_op.drop_index(batch_op.f("ix_ai_providers_is_active"))
        batch_op.drop_index(batch_op.f("ix_ai_providers_created_at"))

    op.drop_table("ai_providers")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_updated_at"))
        batch_op.drop_index(batch_op.f("ix_users_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_users_is_active"))
        batch_op.drop_index(batch_op.f("ix_users_email"))
        batch_op.drop_index(batch_op.f("ix_users_created_at"))

    op.drop_table("users")
    with op.batch_alter_table("units", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_units_updated_at"))
        batch_op.drop_index(batch_op.f("ix_units_symbol"))
        batch_op.drop_index(batch_op.f("ix_units_created_at"))

    op.drop_table("units")
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tenants_updated_at"))
        batch_op.drop_index(batch_op.f("ix_tenants_slug"))
        batch_op.drop_index(batch_op.f("ix_tenants_owner_id"))
        batch_op.drop_index(batch_op.f("ix_tenants_is_active"))
        batch_op.drop_index(batch_op.f("ix_tenants_created_at"))

    op.drop_table("tenants")
    with op.batch_alter_table("telemetry_data", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_telemetry_data_timestamp"))
        batch_op.drop_index("ix_telemetry_data_tenant_slug_ts")
        batch_op.drop_index(batch_op.f("ix_telemetry_data_tenant_id"))
        batch_op.drop_index("ix_telemetry_data_tenant_device_ts")
        batch_op.drop_index(batch_op.f("ix_telemetry_data_patient_id"))
        batch_op.drop_index(batch_op.f("ix_telemetry_data_device_id"))

    op.drop_table("telemetry_data")
    with op.batch_alter_table("system_settings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_system_settings_updated_at"))
        batch_op.drop_index(batch_op.f("ix_system_settings_key"))
        batch_op.drop_index(batch_op.f("ix_system_settings_created_at"))

    op.drop_table("system_settings")
    op.drop_table("system_integrations")
    op.drop_table("instance_settings")
    with op.batch_alter_table("catalog_audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_catalog_audit_type_item")
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_user_id"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_updated_at"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_operation"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_item_id"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_created_at"))
        batch_op.drop_index(batch_op.f("ix_catalog_audit_log_catalog_type"))
        batch_op.drop_index("ix_catalog_audit_created_at")

    op.drop_table("catalog_audit_log")
    with op.batch_alter_table("biomarker_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_biomarker_states_updated_at"))
        batch_op.drop_index(batch_op.f("ix_biomarker_states_slug"))
        batch_op.drop_index(batch_op.f("ix_biomarker_states_created_at"))

    op.drop_table("biomarker_states")
    with op.batch_alter_table("anatomy_figures", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_anatomy_figures_updated_at"))
        batch_op.drop_index(batch_op.f("ix_anatomy_figures_slug"))
        batch_op.drop_index(batch_op.f("ix_anatomy_figures_figure_key"))
        batch_op.drop_index(batch_op.f("ix_anatomy_figures_created_at"))
        batch_op.drop_index("ix_anatomy_figure_group")

    op.drop_table("anatomy_figures")

    # ### end Alembic commands ###

    # Drop the enum types created inline by sa.Enum(...) above. Scoped to the
    # public namespace; the database is left otherwise empty.
    op.execute(
        """
        DO $$ DECLARE r RECORD; BEGIN
        FOR r IN SELECT t.typname
                 FROM pg_type t
                 JOIN pg_enum e ON e.enumtypid = t.oid
                 WHERE t.typnamespace = 'public'::regnamespace
                 GROUP BY t.typname LOOP
          EXECUTE 'DROP TYPE IF EXISTS ' || r.typname || ' CASCADE';
        END LOOP; END $$;
        """
    )

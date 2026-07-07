"""initial schema (squashed)

Consolidates the prior migration chain into a single deterministic baseline.
Generated via ``alembic revision --autogenerate`` against the current model
metadata, then hand-augmented with: required extensions, idempotent enum
creation, TimescaleDB hypertable DDL (guarded), continuous aggregates, and
seed data for examination categories using deterministic UUIDs.

To migrate an existing (pre-squash) database: drop and recreate it, then run
``alembic upgrade head``. There is no in-place upgrade path from the prior
chain; the historical migrations have been archived.

Revision ID: 0001
Revises:
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types used in the schema. Each is created via a DO block so the
# migration is re-runnable (CREATE TYPE lacks IF NOT EXISTS). Inline
# sa.Enum() calls below use create_type=False to avoid duplicate creation.
ENUM_TYPES = [
    ("aiscope", "('SYSTEM', 'TENANT', 'USER', 'ORGANIZATION')"),
    ("allergycategory", "('FOOD', 'MEDICATION', 'ENVIRONMENT', 'BIOLOGIC', 'OTHER')"),
    ("allergyclinicalstatus", "('ACTIVE', 'INACTIVE', 'RESOLVED')"),
    ("allergycriticality", "('LOW', 'HIGH', 'UNABLE_TO_ASSESS')"),
    ("clinicaleventstatus", "('ACTIVE', 'RESOLVED', 'ON_HOLD', 'UNKNOWN')"),
    ("codingsystem", "('LOINC', 'SNOMED', 'CUSTOM')"),
    ("exportscope", "('patient', 'group', 'system')"),
    ("exporttype", "('fhir_only', 'full_backup', 'catalog_only')"),
    ("gender", "('MALE', 'FEMALE', 'OTHER', 'UNKNOWN')"),
    ("integrationstatus", "('PENDING', 'ACTIVE', 'EXPIRED', 'ERROR')"),
    ("jobstatus", "('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'PARTIAL')"),
    ("medicationintent", "('statement', 'order', 'plan', 'proposal')"),
    ("medicationstatus", "('ACTIVE', 'INACTIVE', 'COMPLETED', 'CANCELLED', 'ENTERED_IN_ERROR', 'INTENDED', 'STOPPED', 'ON_HOLD', 'UNKNOWN')"),
    ("notificationchannel", "('IN_APP', 'PUSH', 'EMAIL', 'SMS')"),
    ("notificationstatus", "('PENDING', 'SENT', 'DELIVERED', 'FAILED')"),
    ("notificationtype", "('MEDICATION_REMINDER', 'EXAMINATION_REMINDER', 'BIOMARKER_ALERT', 'BIOMARKER_THRESHOLD', 'OUT_OF_RANGE', 'CALENDAR_EVENT', 'AI_SUGGESTION', 'HITL_TASK', 'AGENT_RESULT', 'INTEGRATION_EVENT', 'SYNC_FAILURE', 'SYSTEM_UPDATE', 'SYSTEM_BROADCAST', 'SYSTEM_ERROR', 'CLINICAL_EVENT', 'CUSTOM')"),
    ("notificationsource", "('SYSTEM', 'INTEGRATION', 'AGENT', 'RULE', 'CLINICAL', 'SCHEDULED')"),
    ("notificationcategory", "('reminder', 'alert', 'hitl', 'agent', 'system', 'integration', 'clinical_event')"),
    ("notificationseverity", "('info', 'warning', 'critical')"),
    ("recipientkind", "('USER', 'PATIENT', 'DOCTOR', 'TENANT', 'SYSTEM')"),
    ("recipientstatus", "('unread', 'read', 'dismissed')"),
    ("notificationruletype", "('BIOMARKER_THRESHOLD', 'OUT_OF_NORMAL_RANGE', 'TREND_ANOMALY', 'EVENT_LIFECYCLE')"),
    ("comparisonoperator", "('>', '<', '>=', '<=', '==', 'out_of_normal')"),
    ("organizationtype", "('HOUSEHOLD', 'CLINIC', 'DEPARTMENT', 'PROVIDER_GROUP', 'HOSPITAL', 'OTHER')"),
    ("quantitytype", "('MASS_CONCENTRATION', 'MOLAR_CONCENTRATION', 'NUMBER_CONCENTRATION', 'PERCENTAGE', 'PRESSURE', 'VOLUME', 'MASS', 'TIME', 'RATIO', 'TEMPERATURE', 'OTHER')"),
    ("role", "('SYSTEM_ADMIN', 'ADMIN', 'MANAGER', 'USER')"),
    ("triggertype", "('TIME', 'RECURRING', 'EVENT', 'THRESHOLD')"),
]


def _create_extension(name: str) -> None:
    """Create a Postgres extension idempotently."""
    op.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")


def _create_enum(name: str, values: str) -> None:
    """Create a Postgres enum type idempotently.

    Postgres lacks ``CREATE TYPE ... IF NOT EXISTS``; the DO block catches the
    duplicate_object error so the migration is re-runnable.
    """
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM {values}; "
        f"EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )


def _drop_enum(name: str) -> None:
    op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")


def _has_timescaledb() -> bool:
    """Check whether the TimescaleDB extension is available."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


def upgrade() -> None:
    """Upgrade schema."""
    # --- Extensions -------------------------------------------------------
    _create_extension("pgcrypto")
    _create_extension("pg_trgm")
    # TimescaleDB is optional; hypertable DDL is guarded separately below.
    _create_extension("timescaledb CASCADE")

    # --- Enum types (idempotent) -----------------------------------------
    for enum_name, enum_values in ENUM_TYPES:
        _create_enum(enum_name, enum_values)

    # --- Tables -----------------------------------------------------------
    op.create_table('ai_providers',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('scope', postgresql.ENUM('SYSTEM', 'TENANT', 'USER', 'ORGANIZATION', name='aiscope', create_type=False), nullable=False),
    sa.Column('provider_type', sa.String(length=50), nullable=False),
    sa.Column('api_base', sa.String(length=500), nullable=False),
    sa.Column('api_key', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_local', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('company_name', sa.String(length=200), nullable=True),
    sa.Column('company_website', sa.String(length=500), nullable=True),
    sa.Column('company_country', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_providers', schema=None) as batch_op:
        batch_op.create_index('idx_ai_providers_scope', ['scope'], unique=False)
        batch_op.create_index('idx_ai_providers_tenant_active', ['tenant_id', 'is_active'], unique=False)
        batch_op.create_index('idx_ai_providers_user', ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_is_active'), ['is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_scope'), ['scope'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_providers_user_id'), ['user_id'], unique=False)

    op.create_table('allergy_catalog',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('category', postgresql.ENUM('FOOD', 'MEDICATION', 'ENVIRONMENT', 'BIOLOGIC', 'OTHER', name='allergycategory', create_type=False), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('typical_reactions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('allergy_catalog', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_allergy_catalog_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_allergy_catalog_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_allergy_catalog_updated_at'), ['updated_at'], unique=False)

    op.create_table('audit_logs',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('resource_type', sa.String(length=100), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=True),
    sa.Column('old_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_user_id'), ['user_id'], unique=False)

    op.create_table('body_parts',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('snomed_code', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_custom', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('body_parts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_body_parts_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_body_parts_slug'), ['slug'], unique=False)
        batch_op.create_index(batch_op.f('ix_body_parts_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_body_parts_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_diagnostic_reports',
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('category', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('code', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('subject', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('effective_datetime', sa.DateTime(timezone=True), nullable=True),
    sa.Column('issued', sa.DateTime(timezone=True), nullable=True),
    sa.Column('performer', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('conclusion', sa.String(), nullable=True),
    sa.Column('conclusion_code', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('presented_form', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_diagnostic_reports', schema=None) as batch_op:
        batch_op.create_index('idx_diagnostic_report_tenant_date', ['tenant_id', 'effective_datetime'], unique=False)
        batch_op.create_index('idx_diagnostic_report_tenant_patient', ['tenant_id', 'subject'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_diagnostic_reports_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_diagnostic_reports_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_diagnostic_reports_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_diagnostic_reports_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_organizations',
    sa.Column('active', sa.Boolean(), nullable=True),
    sa.Column('type', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('org_type', postgresql.ENUM('HOUSEHOLD', 'CLINIC', 'DEPARTMENT', 'PROVIDER_GROUP', 'HOSPITAL', 'OTHER', name='organizationtype', create_type=False), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('alias', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('telecom', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('address', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('part_of_id', sa.UUID(), nullable=True),
    sa.Column('contact', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['part_of_id'], ['fhir_organizations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_organizations', schema=None) as batch_op:
        batch_op.create_index('idx_organization_tenant_name', ['tenant_id', 'name'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_organizations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_organizations_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_organizations_org_type'), ['org_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_organizations_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_organizations_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_provenance',
    sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recorded', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activity', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('agent', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('entity', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_provenance', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fhir_provenance_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_provenance_recorded'), ['recorded'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_provenance_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_provenance_updated_at'), ['updated_at'], unique=False)

    op.create_table('medication_catalog',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('indications', sa.Text(), nullable=True),
    sa.Column('side_effects', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('contraindications', sa.Text(), nullable=True),
    sa.Column('dosage_info', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('medication_catalog', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_medication_catalog_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_medication_catalog_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_medication_catalog_updated_at'), ['updated_at'], unique=False)

    op.create_table('system_integrations',
    sa.Column('domain', sa.String(length=50), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('global_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.PrimaryKeyConstraint('domain')
    )
    op.create_table('system_settings',
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_system_settings_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_system_settings_key'), ['key'], unique=True)
        batch_op.create_index(batch_op.f('ix_system_settings_updated_at'), ['updated_at'], unique=False)

    op.create_table('task_logs',
    sa.Column('task_name', sa.String(length=100), nullable=False),
    sa.Column('task_id', sa.String(length=100), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=True),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('stage', sa.String(length=50), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_logs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_logs_level'), ['level'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_logs_resource_id'), ['resource_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_logs_task_id'), ['task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_logs_task_name'), ['task_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_logs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('telemetry_data',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('device_id', sa.String(length=255), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('heart_rate', sa.Float(), nullable=True),
    sa.Column('steps', sa.Float(), nullable=True),
    sa.Column('calories', sa.Float(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id', 'timestamp')
    )
    with op.batch_alter_table('telemetry_data', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_telemetry_data_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_telemetry_data_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_telemetry_data_timestamp'), ['timestamp'], unique=False)

    op.create_table('tenants',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('units',
    sa.Column('symbol', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('quantity_type', postgresql.ENUM('MASS_CONCENTRATION', 'MOLAR_CONCENTRATION', 'NUMBER_CONCENTRATION', 'PERCENTAGE', 'PRESSURE', 'VOLUME', 'MASS', 'TIME', 'RATIO', 'TEMPERATURE', 'OTHER', name='quantitytype', create_type=False), nullable=False),
    sa.Column('base_unit_id', sa.UUID(), nullable=True),
    sa.Column('conversion_multiplier', sa.Float(), nullable=False),
    sa.Column('dashboard_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['base_unit_id'], ['units.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('units', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_units_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_units_symbol'), ['symbol'], unique=True)
        batch_op.create_index(batch_op.f('ix_units_updated_at'), ['updated_at'], unique=False)

    op.create_table('ai_models',
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('model_name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('max_tokens', sa.Integer(), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('is_local', sa.Boolean(), nullable=True),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['provider_id'], ['ai_providers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_models', schema=None) as batch_op:
        batch_op.create_index('idx_ai_models_provider_active', ['provider_id', 'is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_models_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_models_is_active'), ['is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_models_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_models_provider_id'), ['provider_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_models_updated_at'), ['updated_at'], unique=False)

    op.create_table('biomarker_definitions',
    sa.Column('slug', sa.String(length=255), nullable=False),
    sa.Column('coding_system', postgresql.ENUM('LOINC', 'SNOMED', 'CUSTOM', name='codingsystem', create_type=False), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('preferred_unit_id', sa.UUID(), nullable=True),
    sa.Column('aliases', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('info', sa.Text(), nullable=True),
    sa.Column('reference_range_min', sa.Float(), nullable=True),
    sa.Column('reference_range_max', sa.Float(), nullable=True),
    sa.Column('is_telemetry', sa.Boolean(), nullable=False),
    sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['preferred_unit_id'], ['units.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('biomarker_definitions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_biomarker_definitions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_definitions_slug'), ['slug'], unique=True)
        batch_op.create_index(batch_op.f('ix_biomarker_definitions_updated_at'), ['updated_at'], unique=False)

    op.create_table('biomarker_groups',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('type', sa.String(length=100), nullable=True),
    sa.Column('display_order', sa.Integer(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('biomarker_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_biomarker_groups_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_groups_updated_at'), ['updated_at'], unique=False)

    op.create_table('clinical_event_categories',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('icon', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('color', sa.String(length=50), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    with op.batch_alter_table('clinical_event_categories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clinical_event_categories_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_event_categories_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_event_categories_updated_at'), ['updated_at'], unique=False)

    op.create_table('examination_categories',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('color', sa.String(length=20), nullable=True),
    sa.Column('icon', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    sa.UniqueConstraint('slug')
    )
    with op.batch_alter_table('examination_categories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_examination_categories_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_examination_categories_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examination_categories_updated_at'), ['updated_at'], unique=False)

    op.create_table('laboratories',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('standard_rating', sa.Float(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('laboratories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_laboratories_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_laboratories_updated_at'), ['updated_at'], unique=False)

    op.create_table('users',
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('role', postgresql.ENUM('SYSTEM_ADMIN', 'ADMIN', 'MANAGER', 'USER', name='role', create_type=False), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('ai_task_assignments',
    sa.Column('task_type', sa.String(length=50), nullable=False),
    sa.Column('scope', postgresql.ENUM('SYSTEM', 'TENANT', 'USER', 'ORGANIZATION', name='aiscope', create_type=False), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=True),
    sa.Column('model_id', sa.UUID(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['model_id'], ['ai_models.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['provider_id'], ['ai_providers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_task_assignments', schema=None) as batch_op:
        batch_op.create_index('idx_ai_task_assignments_priority', ['priority'], unique=False)
        batch_op.create_index('idx_ai_task_assignments_scope', ['scope'], unique=False)
        batch_op.create_index('idx_ai_task_assignments_tenant_task', ['tenant_id', 'task_type', 'is_active'], unique=False)
        batch_op.create_index('idx_ai_task_assignments_user', ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_is_active'), ['is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_scope'), ['scope'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_task_type'), ['task_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_task_assignments_user_id'), ['user_id'], unique=False)

    op.create_table('biomarker_group_members',
    sa.Column('group_id', sa.UUID(), nullable=False),
    sa.Column('biomarker_id', sa.UUID(), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['biomarker_id'], ['biomarker_definitions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['biomarker_groups.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('biomarker_group_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_biomarker_group_members_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_group_members_updated_at'), ['updated_at'], unique=False)

    op.create_table('biomarker_relationships',
    sa.Column('source_biomarker_id', sa.UUID(), nullable=False),
    sa.Column('target_biomarker_id', sa.UUID(), nullable=False),
    sa.Column('relation_type', sa.String(length=100), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['source_biomarker_id'], ['biomarker_definitions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_biomarker_id'], ['biomarker_definitions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('biomarker_relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_biomarker_relationships_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_relationships_updated_at'), ['updated_at'], unique=False)

    op.create_table('clinical_event_types',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('icon', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('color', sa.String(length=50), nullable=True),
    sa.Column('metadata_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['clinical_event_categories.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    with op.batch_alter_table('clinical_event_types', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clinical_event_types_category_id'), ['category_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_event_types_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_event_types_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_event_types_updated_at'), ['updated_at'], unique=False)

    op.create_table('doctors',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('specialty', sa.String(length=255), nullable=True),
    sa.Column('license_number', sa.String(length=100), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('telecom', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('address', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('office_number', sa.String(length=50), nullable=True),
    sa.Column('office_details', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('doctors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_doctors_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_doctors_user_id'), ['user_id'], unique=False)

    op.create_table('export_jobs',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('scope', postgresql.ENUM('patient', 'group', 'system', name='exportscope', create_type=False), nullable=False),
    sa.Column('export_type', postgresql.ENUM('fhir_only', 'full_backup', 'catalog_only', name='exporttype', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'PARTIAL', name='jobstatus', create_type=False), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('patient_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('file_path', sa.Text(), nullable=True),
    sa.Column('manifest_path', sa.Text(), nullable=True),
    sa.Column('file_size_bytes', sa.Integer(), nullable=True),
    sa.Column('resource_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('smart_scope', sa.String(length=255), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_export_jobs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_export_jobs_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_export_jobs_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_export_jobs_user_id'), ['user_id'], unique=False)

    op.create_table('fhir_patients',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('name', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('gender', postgresql.ENUM('MALE', 'FEMALE', 'OTHER', 'UNKNOWN', name='gender', create_type=False), nullable=False),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('deceased_boolean', sa.Boolean(), nullable=True),
    sa.Column('deceased_datetime', sa.DateTime(timezone=True), nullable=True),
    sa.Column('address', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('telecom', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('mrn', sa.String(), nullable=True),
    sa.Column('emergency_contact', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('dashboard_layout', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mrn')
    )
    with op.batch_alter_table('fhir_patients', schema=None) as batch_op:
        batch_op.create_index('idx_patient_tenant_mrn', ['tenant_id', 'mrn'], unique=False)
        batch_op.create_index('idx_patient_tenant_name', ['tenant_id', 'name'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_patients_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_patients_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_patients_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_patients_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_patients_user_id'), ['user_id'], unique=False)

    op.create_table('import_jobs',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('source_filename', sa.String(length=255), nullable=True),
    sa.Column('status', postgresql.ENUM('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'PARTIAL', name='jobstatus', create_type=False), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('total_records', sa.Integer(), nullable=False),
    sa.Column('processed_records', sa.Integer(), nullable=False),
    sa.Column('failed_records', sa.Integer(), nullable=False),
    sa.Column('restore_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_jobs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_jobs_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_jobs_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_jobs_user_id'), ['user_id'], unique=False)

    op.create_table('notification_subscriptions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.String(length=255), nullable=True),
    sa.Column('subscription_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notification_subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_subscriptions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_subscriptions_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_subscriptions_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_subscriptions_user_id'), ['user_id'], unique=False)

    op.create_table('biomarker_event_correlations',
    sa.Column('biomarker_id', sa.UUID(), nullable=False),
    sa.Column('event_type_id', sa.UUID(), nullable=False),
    sa.Column('correlation_type', sa.String(length=100), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['biomarker_id'], ['biomarker_definitions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_type_id'], ['clinical_event_types.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('biomarker_event_correlations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_biomarker_event_correlations_biomarker_id'), ['biomarker_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_event_correlations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_event_correlations_event_type_id'), ['event_type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_biomarker_event_correlations_updated_at'), ['updated_at'], unique=False)

    op.create_table('chat_sessions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_sessions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('clinical_events',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('type_id', sa.UUID(), nullable=True),
    sa.Column('status', postgresql.ENUM('ACTIVE', 'RESOLVED', 'ON_HOLD', 'UNKNOWN', name='clinicaleventstatus', create_type=False), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('onset_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('occurrences', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('event_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('coding_system', postgresql.ENUM('LOINC', 'SNOMED', 'CUSTOM', name='codingsystem', create_type=False), nullable=True),
    sa.Column('code', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['type_id'], ['clinical_event_types.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('clinical_events', schema=None) as batch_op:
        batch_op.create_index('idx_clinical_event_patient_type', ['patient_id', 'type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_onset_date'), ['onset_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_resolved_date'), ['resolved_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_type_id'), ['type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_clinical_events_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_allergy_intolerances',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('clinical_status', postgresql.ENUM('ACTIVE', 'INACTIVE', 'RESOLVED', name='allergyclinicalstatus', create_type=False), nullable=False),
    sa.Column('verification_status', sa.String(length=50), nullable=True),
    sa.Column('category', postgresql.ENUM('FOOD', 'MEDICATION', 'ENVIRONMENT', 'BIOLOGIC', 'OTHER', name='allergycategory', create_type=False), nullable=True),
    sa.Column('criticality', postgresql.ENUM('LOW', 'HIGH', 'UNABLE_TO_ASSESS', name='allergycriticality', create_type=False), nullable=True),
    sa.Column('code', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('onset_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_occurrence', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('reactions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_allergy_intolerances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fhir_allergy_intolerances_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_allergy_intolerances_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_allergy_intolerances_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_allergy_intolerances_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_allergy_intolerances_updated_at'), ['updated_at'], unique=False)

    op.create_table('notification_triggers',
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('trigger_type', postgresql.ENUM('TIME', 'RECURRING', 'EVENT', 'THRESHOLD', name='triggertype', create_type=False), nullable=False),
    sa.Column('notification_type', postgresql.ENUM('MEDICATION_REMINDER', 'EXAMINATION_REMINDER', 'BIOMARKER_ALERT', 'BIOMARKER_THRESHOLD', 'OUT_OF_RANGE', 'CALENDAR_EVENT', 'AI_SUGGESTION', 'HITL_TASK', 'AGENT_RESULT', 'INTEGRATION_EVENT', 'SYNC_FAILURE', 'SYSTEM_UPDATE', 'SYSTEM_BROADCAST', 'SYSTEM_ERROR', 'CLINICAL_EVENT', 'CUSTOM', name='notificationtype', create_type=False), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=True),
    sa.Column('last_triggered', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_trigger', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reference_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notification_triggers', schema=None) as batch_op:
        batch_op.create_index('idx_trigger_next_run', ['next_trigger', 'enabled'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_next_trigger'), ['next_trigger'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_reference_id'), ['reference_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_triggers_updated_at'), ['updated_at'], unique=False)

    op.create_table('organization_doctors',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('doctor_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['fhir_organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('organization_id', 'doctor_id')
    )
    op.create_table('patient_layouts',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('layout_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cards_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('patient_layouts', schema=None) as batch_op:
        batch_op.create_index('idx_layout_user_patient', ['user_id', 'patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_layouts_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_layouts_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_patient_layouts_user_id'), ['user_id'], unique=False)

    op.create_table('user_integrations',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('status', postgresql.ENUM('PENDING', 'ACTIVE', 'EXPIRED', 'ERROR', name='integrationstatus', create_type=False), nullable=True),
    sa.Column('access_token', sa.Text(), nullable=True),
    sa.Column('refresh_token', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scopes', sa.String(length=1000), nullable=True),
    sa.Column('provider_account_id', sa.String(length=255), nullable=True),
    sa.Column('instance_name', sa.String(length=255), nullable=True),
    sa.Column('is_debug_enabled', sa.Boolean(), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_integrations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_integrations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_integrations_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_integrations_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_integrations_user_id'), ['user_id'], unique=False)

    op.create_table('chat_messages',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tasks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_messages_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_messages_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_messages_updated_at'), ['updated_at'], unique=False)

    op.create_table('examinations',
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('examination_date', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('patient_notes', sa.Text(), nullable=True),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('source_integration_id', sa.UUID(), nullable=True),
    sa.Column('external_id', sa.String(), nullable=True),
    sa.Column('auto_extract_metadata', sa.Boolean(), nullable=True),
    sa.Column('diagnoses', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('impressions', sa.Text(), nullable=True),
    sa.Column('extraction_status', sa.String(length=50), nullable=True),
    sa.Column('extraction_progress', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['examination_categories.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['fhir_organizations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_integration_id'], ['user_integrations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('examinations', schema=None) as batch_op:
        batch_op.create_index('idx_exam_tenant_patient', ['tenant_id', 'patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_category_id'), ['category_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_examination_date'), ['examination_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_source_integration_id'), ['source_integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_examinations_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_devices',
    sa.Column('identifier', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('device_name', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('type', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('manufacturer', sa.String(length=255), nullable=True),
    sa.Column('model_number', sa.String(length=255), nullable=True),
    sa.Column('serial_number', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('owner_integration_id', sa.UUID(), nullable=True),
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_integration_id'], ['user_integrations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fhir_devices_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_devices_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_devices_owner_integration_id'), ['owner_integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_devices_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_devices_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_devices_updated_at'), ['updated_at'], unique=False)

    op.create_table('integration_debug_logs',
    sa.Column('integration_id', sa.UUID(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['integration_id'], ['user_integrations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('integration_debug_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integration_debug_logs_integration_id'), ['integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_integration_debug_logs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('integration_sync_logs',
    sa.Column('integration_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('records_synced', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['integration_id'], ['user_integrations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('integration_sync_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integration_sync_logs_integration_id'), ['integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_integration_sync_logs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('file_path', sa.String(length=500), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('examination_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('progress', sa.Integer(), nullable=True),
    sa.Column('extracted_text', sa.Text(), nullable=True),
    sa.Column('entities', sa.JSON(), nullable=True),
    sa.Column('include_in_extraction', sa.Boolean(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('is_edited', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['examination_id'], ['examinations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index('idx_doc_tenant_owner', ['tenant_id', 'owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_examination_id'), ['examination_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_filename'), ['filename'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_parent_id'), ['parent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_updated_at'), ['updated_at'], unique=False)

    op.create_table('event_examination_links',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('examination_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['clinical_events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['examination_id'], ['examinations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('event_examination_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_examination_links_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_examination_links_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_examination_links_examination_id'), ['examination_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_examination_links_updated_at'), ['updated_at'], unique=False)

    op.create_table('examination_doctors',
    sa.Column('examination_id', sa.UUID(), nullable=False),
    sa.Column('doctor_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['examination_id'], ['examinations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('examination_id', 'doctor_id')
    )
    op.create_table('fhir_communications',
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('category', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('priority', sa.String(length=50), nullable=True),
    sa.Column('subject_patient_id', sa.UUID(), nullable=True),
    sa.Column('encounter_id', sa.UUID(), nullable=True),
    sa.Column('topic', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('sent', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sender', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('recipient', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['encounter_id'], ['examinations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['subject_patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_communications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fhir_communications_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_communications_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_communications_encounter_id'), ['encounter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_communications_subject_patient_id'), ['subject_patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_communications_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_communications_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_medications',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('examination_id', sa.UUID(), nullable=True),
    sa.Column('status', postgresql.ENUM('ACTIVE', 'INACTIVE', 'COMPLETED', 'CANCELLED', 'ENTERED_IN_ERROR', 'INTENDED', 'STOPPED', 'ON_HOLD', 'UNKNOWN', name='medicationstatus', create_type=False), nullable=False),
    sa.Column('intent', postgresql.ENUM('STATEMENT', 'ORDER', 'PLAN', 'PROPOSAL', name='medicationintent', create_type=False), nullable=False),
    sa.Column('code', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('dosage', sa.String(length=255), nullable=True),
    sa.Column('frequency', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('subject', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['examination_id'], ['examinations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_medications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fhir_medications_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_examination_id'), ['examination_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_intent'), ['intent'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_medications_updated_at'), ['updated_at'], unique=False)

    op.create_table('fhir_observations',
    sa.Column('document_id', sa.String(), nullable=True),
    sa.Column('examination_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('category', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('code', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('subject', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('effective_datetime', sa.DateTime(timezone=True), nullable=True),
    sa.Column('value_quantity', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('value_string', sa.String(), nullable=True),
    sa.Column('value_codeableConcept', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reference_range', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('interpretation', sa.String(), nullable=True),
    sa.Column('comment', sa.String(), nullable=True),
    sa.Column('performer', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('biomarker_id', sa.UUID(), nullable=True),
    sa.Column('lab_id', sa.UUID(), nullable=True),
    sa.Column('method', sa.String(length=255), nullable=True),
    sa.Column('raw_value', sa.Float(), nullable=True),
    sa.Column('raw_unit_id', sa.UUID(), nullable=True),
    sa.Column('normalized_value', sa.Float(), nullable=True),
    sa.Column('relative_score', sa.Float(), nullable=True),
    sa.Column('lab_reference_range', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['biomarker_id'], ['biomarker_definitions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['examination_id'], ['examinations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lab_id'], ['laboratories.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['raw_unit_id'], ['units.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('fhir_observations', schema=None) as batch_op:
        batch_op.create_index('idx_observation_tenant_code', ['tenant_id', 'code'], unique=False)
        batch_op.create_index('idx_observation_tenant_date', ['tenant_id', 'effective_datetime'], unique=False)
        batch_op.create_index('idx_observation_tenant_patient', ['tenant_id', 'subject'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_biomarker_id'), ['biomarker_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_deleted_at'), ['deleted_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_examination_id'), ['examination_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_lab_id'), ['lab_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_raw_unit_id'), ['raw_unit_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fhir_observations_updated_at'), ['updated_at'], unique=False)

    op.create_table('event_observation_links',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('observation_id', sa.UUID(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['clinical_events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['observation_id'], ['fhir_observations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('event_observation_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_observation_links_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_observation_links_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_observation_links_observation_id'), ['observation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_observation_links_updated_at'), ['updated_at'], unique=False)

    op.create_table('notifications',
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('trigger_id', sa.UUID(), nullable=True),
    sa.Column('communication_id', sa.UUID(), nullable=True),
    sa.Column('source', postgresql.ENUM('SYSTEM', 'INTEGRATION', 'AGENT', 'RULE', 'CLINICAL', 'SCHEDULED', name='notificationsource', create_type=False), nullable=False),
    sa.Column('type', postgresql.ENUM('MEDICATION_REMINDER', 'EXAMINATION_REMINDER', 'BIOMARKER_ALERT', 'BIOMARKER_THRESHOLD', 'OUT_OF_RANGE', 'CALENDAR_EVENT', 'AI_SUGGESTION', 'HITL_TASK', 'AGENT_RESULT', 'INTEGRATION_EVENT', 'SYNC_FAILURE', 'SYSTEM_UPDATE', 'SYSTEM_BROADCAST', 'SYSTEM_ERROR', 'CLINICAL_EVENT', 'CUSTOM', name='notificationtype', create_type=False), nullable=False),
    sa.Column('category', postgresql.ENUM('reminder', 'alert', 'hitl', 'agent', 'system', 'integration', 'clinical_event', name='notificationcategory', create_type=False), nullable=False),
    sa.Column('severity', postgresql.ENUM('info', 'warning', 'critical', name='notificationseverity', create_type=False), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_ref', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('sender_user_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['communication_id'], ['fhir_communications.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trigger_id'], ['notification_triggers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_communication_id'), ['communication_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_sender_user_id'), ['sender_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_source'), ['source'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_type'), ['type'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_updated_at'), ['updated_at'], unique=False)

    op.create_table('notification_recipients',
    sa.Column('notification_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('recipient_kind', postgresql.ENUM('USER', 'PATIENT', 'DOCTOR', 'TENANT', 'SYSTEM', name='recipientkind', create_type=False), nullable=False),
    sa.Column('recipient_ref', sa.UUID(), nullable=True),
    sa.Column('status', postgresql.ENUM('unread', 'read', 'dismissed', name='recipientstatus', create_type=False), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notification_recipients', schema=None) as batch_op:
        batch_op.create_index('idx_notification_recipient_user_status', ['user_id', 'status'], unique=False)
        batch_op.create_index('idx_notification_recipient_tenant_status', ['tenant_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_recipients_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_recipients_notification_id'), ['notification_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_recipients_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_recipients_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_recipients_user_id'), ['user_id'], unique=False)

    op.create_table('notification_deliveries',
    sa.Column('notification_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('channel', postgresql.ENUM('IN_APP', 'PUSH', 'EMAIL', 'SMS', name='notificationchannel', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM('PENDING', 'SENT', 'DELIVERED', 'FAILED', name='notificationstatus', create_type=False), nullable=False),
    sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('subscription_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subscription_id'], ['notification_subscriptions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notification_deliveries', schema=None) as batch_op:
        batch_op.create_index('idx_notification_delivery_lookup', ['notification_id', 'channel'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_deliveries_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_deliveries_notification_id'), ['notification_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_deliveries_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_deliveries_updated_at'), ['updated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_deliveries_user_id'), ['user_id'], unique=False)

    op.create_table('notification_rules',
    sa.Column('rule_type', postgresql.ENUM('BIOMARKER_THRESHOLD', 'OUT_OF_NORMAL_RANGE', 'TREND_ANOMALY', 'EVENT_LIFECYCLE', name='notificationruletype', create_type=False), nullable=False),
    sa.Column('biomarker_id', sa.UUID(), nullable=True),
    sa.Column('operator', postgresql.ENUM('>', '<', '>=', '<=', '==', 'out_of_normal', name='comparisonoperator', create_type=False), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('patient_id', sa.UUID(), nullable=True),
    sa.Column('severity', postgresql.ENUM('info', 'warning', 'critical', name='notificationseverity', create_type=False), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('cooldown_minutes', sa.Integer(), nullable=False),
    sa.Column('last_fired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('title_template', sa.String(length=255), nullable=True),
    sa.Column('body_template', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['biomarker_id'], ['biomarker_definitions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['fhir_patients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notification_rules', schema=None) as batch_op:
        batch_op.create_index('idx_notification_rule_lookup', ['tenant_id', 'biomarker_id', 'enabled'], unique=False)
        batch_op.create_index('idx_notification_rule_patient', ['patient_id', 'enabled'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_biomarker_id'), ['biomarker_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_last_fired_at'), ['last_fired_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_patient_id'), ['patient_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_rule_type'), ['rule_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_rules_updated_at'), ['updated_at'], unique=False)

    # --- Post-table DDL: TimescaleDB hypertable + continuous aggregates ----
    # All TimescaleDB DDL is guarded so the migration succeeds on plain-PG
    # dev/test databases where the extension is unavailable.
    if _has_timescaledb():
        # Convert telemetry_data to a hypertable keyed by its timestamp column.
        op.execute(
            "SELECT create_hypertable('telemetry_data', 'timestamp', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        # Compression settings (segment by device_id, order by timestamp desc).
        op.execute(
            "ALTER TABLE telemetry_data SET ("
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'device_id', "
            "timescaledb.compress_orderby = 'timestamp DESC'"
            ")"
        )
        op.execute(
            "SELECT add_compression_policy('telemetry_data', "
            "INTERVAL '7 days', if_not_exists => true)"
        )
        op.execute(
            "SELECT add_retention_policy('telemetry_data', "
            "INTERVAL '2 years', if_not_exists => true)"
        )
        # Hourly continuous aggregate.
        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_hourly
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket('1 hour', timestamp) AS bucket,
                tenant_id,
                device_id,
                AVG(heart_rate) AS heart_rate_avg,
                MIN(heart_rate) AS heart_rate_min,
                MAX(heart_rate) AS heart_rate_max,
                AVG(steps) AS steps_avg,
                MIN(steps) AS steps_min,
                MAX(steps) AS steps_max,
                AVG(calories) AS calories_avg,
                MIN(calories) AS calories_min,
                MAX(calories) AS calories_max
            FROM telemetry_data
            GROUP BY bucket, tenant_id, device_id
            WITH NO DATA
            """
        )
        op.execute(
            "SELECT add_continuous_aggregate_policy('telemetry_hourly', "
            "start_offset => INTERVAL '3 days', "
            "end_offset => INTERVAL '1 hour', "
            "schedule_interval => INTERVAL '1 hour', "
            "if_not_exists => true)"
        )
        # Daily continuous aggregate.
        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_daily
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket('1 day', timestamp) AS bucket,
                tenant_id,
                device_id,
                AVG(heart_rate) AS heart_rate_avg,
                MIN(heart_rate) AS heart_rate_min,
                MAX(heart_rate) AS heart_rate_max,
                AVG(steps) AS steps_avg,
                MIN(steps) AS steps_min,
                MAX(steps) AS steps_max,
                AVG(calories) AS calories_avg,
                MIN(calories) AS calories_min,
                MAX(calories) AS calories_max
            FROM telemetry_data
            GROUP BY bucket, tenant_id, device_id
            WITH NO DATA
            """
        )
        op.execute(
            "SELECT add_continuous_aggregate_policy('telemetry_daily', "
            "start_offset => INTERVAL '7 days', "
            "end_offset => INTERVAL '1 day', "
            "schedule_interval => INTERVAL '1 day', "
            "if_not_exists => true)"
        )

    # Seed data now lives in backend/data/seeds/*.json — loaded by SeedService
    # at application startup (see app/main.py lifespan). The old
    # _seed_examination_categories() inline seeder was removed when the table
    # was consolidated into the unified concepts table.

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema: drop all tables, then enum types and extensions.

    The DROP TABLE chain handles its own CASCADE for foreign keys. We drop
    extension objects last to preserve constraint validation order.
    """
    # Drop timescaledb objects first (materialized views + policies) but
    # leave telemetry_data itself for the normal DROP TABLE chain below.
    if _has_timescaledb():
        op.execute(
            "SELECT remove_continuous_aggregate_policy('telemetry_daily', "
            "if_exists => true)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_daily CASCADE")
        op.execute(
            "SELECT remove_continuous_aggregate_policy('telemetry_hourly', "
            "if_exists => true)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE")
        op.execute(
            "SELECT remove_retention_policy('telemetry_data', if_exists => true)"
        )
        op.execute(
            "SELECT remove_compression_policy('telemetry_data', if_exists => true)"
        )

    with op.batch_alter_table('notification_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_rules_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_rule_type'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_patient_id'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_last_fired_at'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_created_at'))
        batch_op.drop_index(batch_op.f('ix_notification_rules_biomarker_id'))
        batch_op.drop_index('idx_notification_rule_patient')
        batch_op.drop_index('idx_notification_rule_lookup')

    op.drop_table('notification_rules')
    with op.batch_alter_table('notification_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_user_id'))
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_notification_id'))
        batch_op.drop_index(batch_op.f('ix_notification_deliveries_created_at'))
        batch_op.drop_index('idx_notification_delivery_lookup')

    op.drop_table('notification_deliveries')
    with op.batch_alter_table('notification_recipients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_recipients_user_id'))
        batch_op.drop_index(batch_op.f('ix_notification_recipients_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notification_recipients_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notification_recipients_notification_id'))
        batch_op.drop_index(batch_op.f('ix_notification_recipients_created_at'))
        batch_op.drop_index('idx_notification_recipient_tenant_status')
        batch_op.drop_index('idx_notification_recipient_user_status')

    op.drop_table('notification_recipients')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notifications_type'))
        batch_op.drop_index(batch_op.f('ix_notifications_source'))
        batch_op.drop_index(batch_op.f('ix_notifications_sender_user_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_patient_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))
        batch_op.drop_index(batch_op.f('ix_notifications_communication_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_category'))

    op.drop_table('notifications')
    with op.batch_alter_table('event_observation_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_observation_links_updated_at'))
        batch_op.drop_index(batch_op.f('ix_event_observation_links_observation_id'))
        batch_op.drop_index(batch_op.f('ix_event_observation_links_event_id'))
        batch_op.drop_index(batch_op.f('ix_event_observation_links_created_at'))

    op.drop_table('event_observation_links')
    with op.batch_alter_table('fhir_observations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_observations_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_raw_unit_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_lab_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_examination_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_document_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_created_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_observations_biomarker_id'))
        batch_op.drop_index('idx_observation_tenant_patient')
        batch_op.drop_index('idx_observation_tenant_date')
        batch_op.drop_index('idx_observation_tenant_code')

    op.drop_table('fhir_observations')
    with op.batch_alter_table('fhir_medications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_medications_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_patient_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_intent'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_examination_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_medications_created_at'))

    op.drop_table('fhir_medications')
    with op.batch_alter_table('fhir_communications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_communications_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_communications_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_communications_subject_patient_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_communications_encounter_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_communications_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_communications_created_at'))

    op.drop_table('fhir_communications')
    op.drop_table('examination_doctors')
    with op.batch_alter_table('event_examination_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_examination_links_updated_at'))
        batch_op.drop_index(batch_op.f('ix_event_examination_links_examination_id'))
        batch_op.drop_index(batch_op.f('ix_event_examination_links_event_id'))
        batch_op.drop_index(batch_op.f('ix_event_examination_links_created_at'))

    op.drop_table('event_examination_links')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_updated_at'))
        batch_op.drop_index(batch_op.f('ix_documents_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_documents_status'))
        batch_op.drop_index(batch_op.f('ix_documents_parent_id'))
        batch_op.drop_index(batch_op.f('ix_documents_owner_id'))
        batch_op.drop_index(batch_op.f('ix_documents_filename'))
        batch_op.drop_index(batch_op.f('ix_documents_examination_id'))
        batch_op.drop_index(batch_op.f('ix_documents_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_documents_created_at'))
        batch_op.drop_index('idx_doc_tenant_owner')

    op.drop_table('documents')
    with op.batch_alter_table('integration_sync_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integration_sync_logs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_integration_sync_logs_integration_id'))

    op.drop_table('integration_sync_logs')
    with op.batch_alter_table('integration_debug_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integration_debug_logs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_integration_debug_logs_integration_id'))

    op.drop_table('integration_debug_logs')
    with op.batch_alter_table('fhir_devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_devices_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_devices_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_devices_patient_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_devices_owner_integration_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_devices_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_devices_created_at'))

    op.drop_table('fhir_devices')
    with op.batch_alter_table('examinations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_examinations_updated_at'))
        batch_op.drop_index(batch_op.f('ix_examinations_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_examinations_source_integration_id'))
        batch_op.drop_index(batch_op.f('ix_examinations_organization_id'))
        batch_op.drop_index(batch_op.f('ix_examinations_external_id'))
        batch_op.drop_index(batch_op.f('ix_examinations_examination_date'))
        batch_op.drop_index(batch_op.f('ix_examinations_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_examinations_created_at'))
        batch_op.drop_index(batch_op.f('ix_examinations_category_id'))
        batch_op.drop_index('idx_exam_tenant_patient')

    op.drop_table('examinations')
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_messages_updated_at'))
        batch_op.drop_index(batch_op.f('ix_chat_messages_session_id'))
        batch_op.drop_index(batch_op.f('ix_chat_messages_created_at'))

    op.drop_table('chat_messages')
    with op.batch_alter_table('user_integrations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_integrations_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_integrations_updated_at'))
        batch_op.drop_index(batch_op.f('ix_user_integrations_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_user_integrations_created_at'))

    op.drop_table('user_integrations')
    with op.batch_alter_table('patient_layouts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_layouts_user_id'))
        batch_op.drop_index(batch_op.f('ix_patient_layouts_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_patient_layouts_patient_id'))
        batch_op.drop_index('idx_layout_user_patient')

    op.drop_table('patient_layouts')
    op.drop_table('organization_doctors')
    with op.batch_alter_table('notification_triggers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_triggers_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notification_triggers_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notification_triggers_reference_id'))
        batch_op.drop_index(batch_op.f('ix_notification_triggers_patient_id'))
        batch_op.drop_index(batch_op.f('ix_notification_triggers_next_trigger'))
        batch_op.drop_index(batch_op.f('ix_notification_triggers_created_at'))
        batch_op.drop_index('idx_trigger_next_run')

    op.drop_table('notification_triggers')
    with op.batch_alter_table('fhir_allergy_intolerances', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_allergy_intolerances_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_allergy_intolerances_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_allergy_intolerances_patient_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_allergy_intolerances_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_allergy_intolerances_created_at'))

    op.drop_table('fhir_allergy_intolerances')
    with op.batch_alter_table('clinical_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clinical_events_updated_at'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_type_id'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_resolved_date'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_patient_id'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_onset_date'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_clinical_events_created_at'))
        batch_op.drop_index('idx_clinical_event_patient_type')

    op.drop_table('clinical_events')
    with op.batch_alter_table('chat_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_chat_sessions_updated_at'))
        batch_op.drop_index(batch_op.f('ix_chat_sessions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_chat_sessions_patient_id'))
        batch_op.drop_index(batch_op.f('ix_chat_sessions_created_at'))

    op.drop_table('chat_sessions')
    with op.batch_alter_table('biomarker_event_correlations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_biomarker_event_correlations_updated_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_event_correlations_event_type_id'))
        batch_op.drop_index(batch_op.f('ix_biomarker_event_correlations_created_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_event_correlations_biomarker_id'))

    op.drop_table('biomarker_event_correlations')
    with op.batch_alter_table('notification_subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_subscriptions_user_id'))
        batch_op.drop_index(batch_op.f('ix_notification_subscriptions_updated_at'))
        batch_op.drop_index(batch_op.f('ix_notification_subscriptions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notification_subscriptions_created_at'))

    op.drop_table('notification_subscriptions')
    with op.batch_alter_table('import_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_jobs_user_id'))
        batch_op.drop_index(batch_op.f('ix_import_jobs_updated_at'))
        batch_op.drop_index(batch_op.f('ix_import_jobs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_import_jobs_created_at'))

    op.drop_table('import_jobs')
    with op.batch_alter_table('fhir_patients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_patients_user_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_patients_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_patients_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_patients_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_patients_created_at'))
        batch_op.drop_index('idx_patient_tenant_name')
        batch_op.drop_index('idx_patient_tenant_mrn')

    op.drop_table('fhir_patients')
    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_export_jobs_user_id'))
        batch_op.drop_index(batch_op.f('ix_export_jobs_updated_at'))
        batch_op.drop_index(batch_op.f('ix_export_jobs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_export_jobs_created_at'))

    op.drop_table('export_jobs')
    with op.batch_alter_table('doctors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_doctors_user_id'))
        batch_op.drop_index(batch_op.f('ix_doctors_tenant_id'))

    op.drop_table('doctors')
    with op.batch_alter_table('clinical_event_types', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clinical_event_types_updated_at'))
        batch_op.drop_index(batch_op.f('ix_clinical_event_types_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_clinical_event_types_created_at'))
        batch_op.drop_index(batch_op.f('ix_clinical_event_types_category_id'))

    op.drop_table('clinical_event_types')
    with op.batch_alter_table('biomarker_relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_biomarker_relationships_updated_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_relationships_created_at'))

    op.drop_table('biomarker_relationships')
    with op.batch_alter_table('biomarker_group_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_biomarker_group_members_updated_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_group_members_created_at'))

    op.drop_table('biomarker_group_members')
    with op.batch_alter_table('ai_task_assignments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_user_id'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_updated_at'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_task_type'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_scope'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_is_active'))
        batch_op.drop_index(batch_op.f('ix_ai_task_assignments_created_at'))
        batch_op.drop_index('idx_ai_task_assignments_user')
        batch_op.drop_index('idx_ai_task_assignments_tenant_task')
        batch_op.drop_index('idx_ai_task_assignments_scope')
        batch_op.drop_index('idx_ai_task_assignments_priority')

    op.drop_table('ai_task_assignments')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('laboratories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_laboratories_updated_at'))
        batch_op.drop_index(batch_op.f('ix_laboratories_created_at'))

    op.drop_table('laboratories')
    with op.batch_alter_table('examination_categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_examination_categories_updated_at'))
        batch_op.drop_index(batch_op.f('ix_examination_categories_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_examination_categories_created_at'))

    op.drop_table('examination_categories')
    with op.batch_alter_table('clinical_event_categories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clinical_event_categories_updated_at'))
        batch_op.drop_index(batch_op.f('ix_clinical_event_categories_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_clinical_event_categories_created_at'))

    op.drop_table('clinical_event_categories')
    with op.batch_alter_table('biomarker_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_biomarker_groups_updated_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_groups_created_at'))

    op.drop_table('biomarker_groups')
    with op.batch_alter_table('biomarker_definitions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_biomarker_definitions_updated_at'))
        batch_op.drop_index(batch_op.f('ix_biomarker_definitions_slug'))
        batch_op.drop_index(batch_op.f('ix_biomarker_definitions_created_at'))

    op.drop_table('biomarker_definitions')
    with op.batch_alter_table('ai_models', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_models_updated_at'))
        batch_op.drop_index(batch_op.f('ix_ai_models_provider_id'))
        batch_op.drop_index(batch_op.f('ix_ai_models_name'))
        batch_op.drop_index(batch_op.f('ix_ai_models_is_active'))
        batch_op.drop_index(batch_op.f('ix_ai_models_created_at'))
        batch_op.drop_index('idx_ai_models_provider_active')

    op.drop_table('ai_models')
    with op.batch_alter_table('units', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_units_updated_at'))
        batch_op.drop_index(batch_op.f('ix_units_symbol'))
        batch_op.drop_index(batch_op.f('ix_units_created_at'))

    op.drop_table('units')
    op.drop_table('tenants')
    with op.batch_alter_table('telemetry_data', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_telemetry_data_timestamp'))
        batch_op.drop_index(batch_op.f('ix_telemetry_data_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_telemetry_data_device_id'))

    op.drop_table('telemetry_data')
    with op.batch_alter_table('task_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_logs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_task_logs_task_name'))
        batch_op.drop_index(batch_op.f('ix_task_logs_task_id'))
        batch_op.drop_index(batch_op.f('ix_task_logs_resource_id'))
        batch_op.drop_index(batch_op.f('ix_task_logs_level'))
        batch_op.drop_index(batch_op.f('ix_task_logs_created_at'))

    op.drop_table('task_logs')
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_system_settings_updated_at'))
        batch_op.drop_index(batch_op.f('ix_system_settings_key'))
        batch_op.drop_index(batch_op.f('ix_system_settings_created_at'))

    op.drop_table('system_settings')
    op.drop_table('system_integrations')
    with op.batch_alter_table('medication_catalog', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_medication_catalog_updated_at'))
        batch_op.drop_index(batch_op.f('ix_medication_catalog_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_medication_catalog_created_at'))

    op.drop_table('medication_catalog')
    with op.batch_alter_table('fhir_provenance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_provenance_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_provenance_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_provenance_recorded'))
        batch_op.drop_index(batch_op.f('ix_fhir_provenance_created_at'))

    op.drop_table('fhir_provenance')
    with op.batch_alter_table('fhir_organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_organizations_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_organizations_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_organizations_org_type'))
        batch_op.drop_index(batch_op.f('ix_fhir_organizations_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_organizations_created_at'))
        batch_op.drop_index('idx_organization_tenant_name')

    op.drop_table('fhir_organizations')
    with op.batch_alter_table('fhir_diagnostic_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fhir_diagnostic_reports_updated_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_diagnostic_reports_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_fhir_diagnostic_reports_deleted_at'))
        batch_op.drop_index(batch_op.f('ix_fhir_diagnostic_reports_created_at'))
        batch_op.drop_index('idx_diagnostic_report_tenant_patient')
        batch_op.drop_index('idx_diagnostic_report_tenant_date')

    op.drop_table('fhir_diagnostic_reports')
    with op.batch_alter_table('body_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_body_parts_updated_at'))
        batch_op.drop_index(batch_op.f('ix_body_parts_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_body_parts_slug'))
        batch_op.drop_index(batch_op.f('ix_body_parts_created_at'))

    op.drop_table('body_parts')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_user_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('allergy_catalog', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_allergy_catalog_updated_at'))
        batch_op.drop_index(batch_op.f('ix_allergy_catalog_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_allergy_catalog_created_at'))

    op.drop_table('allergy_catalog')
    with op.batch_alter_table('ai_providers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_providers_user_id'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_updated_at'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_scope'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_name'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_is_active'))
        batch_op.drop_index(batch_op.f('ix_ai_providers_created_at'))
        batch_op.drop_index('idx_ai_providers_user')
        batch_op.drop_index('idx_ai_providers_tenant_active')
        batch_op.drop_index('idx_ai_providers_scope')

    op.drop_table('ai_providers')

    # --- Drop enum types (reverse order, CASCADE handles dependencies) ----
    for enum_name, _ in reversed(ENUM_TYPES):
        _drop_enum(enum_name)

    # --- Drop extensions last (optional objects; left installed otherwise) -
    # Only drop the extensions we own; pgcrypto/pg_trgm/timescaledb may be
    # shared by other databases, so leave them in place.

    # ### end Alembic commands ###

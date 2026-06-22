"""create fhir_devices and fhir_communications tables

Revision ID: 34414d55a822
Revises: c987390e2778
Create Date: 2026-06-21 17:00:00.000000

Audit items C9 (Device) + C15 (Communication): two new first-class FHIR
tables for concepts that have no app-table analog.

fhir_devices:
  Reference table for telemetry devices. Backfilled from user_integrations
  rows. TelemetryDataModel.device_id references Device.id by convention
  (no FK on the TimescaleDB hypertable).

fhir_communications:
  Clinical messaging — NOT push notifications. The existing notifications
  table stays for VAPID/web-push/triggers. A separate column on
  notifications (notification.communication_id) optionally links a push
  notification to its source clinical Communication.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "34414d55a822"
down_revision = "c987390e2778"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- fhir_devices ----
    op.create_table(
        "fhir_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("identifier", JSONB, nullable=True),
        sa.Column("device_name", JSONB, nullable=True),  # [{name, type}]
        sa.Column("type", JSONB, nullable=True),  # CodeableConcept
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("model_number", sa.String(255), nullable=True),
        sa.Column("serial_number", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),  # active|inactive|entered-in-error|unknown
        sa.Column("owner_integration_id", UUID(as_uuid=True), sa.ForeignKey("user_integrations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("patient_id", UUID(as_uuid=True), sa.ForeignKey("fhir_patients.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Backfill one Device per UserIntegration row.
    op.execute(
        """
        INSERT INTO fhir_devices (tenant_id, identifier, device_name, type, status, owner_integration_id, created_at, updated_at)
        SELECT
            ui.tenant_id,
            jsonb_build_array(jsonb_build_object('system', 'urn:health-assistant:integration-id', 'value', ui.id::text)),
            jsonb_build_array(jsonb_build_object('name', COALESCE(ui.instance_name, ui.provider), 'type', 'user-friendly-name')),
            jsonb_build_object('text', ui.provider),
            CASE WHEN ui.status = 'ACTIVE' THEN 'active' ELSE 'inactive' END,
            ui.id,
            NOW(),
            NOW()
        FROM user_integrations ui
        WHERE NOT EXISTS (
            SELECT 1 FROM fhir_devices fd WHERE fd.owner_integration_id = ui.id
        )
        """
    )

    # ---- fhir_communications ----
    op.create_table(
        "fhir_communications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="completed"),  # preparation|in-progress|not-done|on-hold|stopped|completed|entered-in-error|unknown
        sa.Column("category", JSONB, nullable=True),
        sa.Column("priority", sa.String(50), nullable=True),  # routine|urgent|asap|stat
        sa.Column("subject_patient_id", UUID(as_uuid=True), sa.ForeignKey("fhir_patients.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("topic", JSONB, nullable=True),  # CodeableConcept
        sa.Column("payload", JSONB, nullable=True),  # [{contentString: "..."} | {contentAttachment: {...}} | {contentReference: {...}}]
        sa.Column("sent", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sender", JSONB, nullable=True),  # {reference: "Practitioner/uuid"}
        sa.Column("recipient", JSONB, nullable=True),  # [{reference: "Patient/uuid"}]
        sa.Column("encounter_id", UUID(as_uuid=True), sa.ForeignKey("examinations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
    )

    # Optional link from notifications to communications.
    op.execute(
        "ALTER TABLE notifications "
        "ADD COLUMN IF NOT EXISTS communication_id UUID "
        "REFERENCES fhir_communications(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS communication_id")
    op.drop_table("fhir_communications")
    op.drop_table("fhir_devices")

# ruff: noqa: E501 -- long immutable DDL strings; reflow when touched
"""Calendar connector lane: family cloud-calendar bindings (ADR-0052 / plan 52 P2).

Four tables:

**``calendar_bindings``** — one bound provider calendar per
tenant/user/patient: the family credential-binding row (provider, service,
auth kind, label/account label, granted scopes, the ``secret_ref``
**pointer**, direction, redaction profile, enabled, status, sync health,
opaque cursor, options). No column is token-shaped — credential material
lives only in ``calendar_binding_credentials`` slots sealed with the family
at-rest cipher. Closed vocabularies mirrored in CHECK constraints
(direction ``push|pull|two-way``, redaction ``none|neutral|full``, the
family account states, the catalog auth kinds, ``service = 'calendar'``).

**``calendar_event_links``** — sync identity per projected record:
``(binding, uid)`` unique, the local record coordinates
(``local_kind``/``local_id``), the remote ``remote_id``/``etag`` pin and
the **separate** ``local_hash``/``remote_hash`` content pins (plan 52 §10
E3 — providers normalize on write; one shared hash loops forever). No PHI.

**``calendar_binding_credentials``** — the sealed credential slots (the
``integration_credentials`` shape keyed by the binding — a calendar
binding is deliberately not a ``UserIntegration``, plan 52 §3).

**``calendar_sync_logs``** — one audit row per sync run: counts and typed
error codes only, never titles/PHI (ADR-0052 §8).

Two extra laws are enforced as indexes here:

* ``uq_calendar_bindings_remote_calendar`` — one writer per remote calendar
  (ADR-0052 §9 / Rev 3 H4): unique ``(tenant, patient, provider,
  remote_calendar_id)`` among writable bindings; pull-only subscriptions
  may share. ``remote_calendar_id`` is derived and credential-free.
* ``uq_clinical_events_calendar_dedup`` — C3 dedup: a pulled event dedups
  on ``(tenant, patient, external_id)``; the integration framework's
  ``source_integration_id`` index cannot be reused (a binding is not a
  ``UserIntegration``), so this partial index mirrors its shape for
  calendar-sourced rows only.

Downgrade drops the two indexes and all four tables; no data backfill is
attempted (owner doctrine — this program has no users).

Revision ID: 0009_calendar_bindings
Revises: 0008_extension_sources
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# The vocabulary enums are the source of truth the CHECK constraints derive
# from (test_enum_check_parity). Declared here so the migration and the model
# cannot drift; `native_enum=False` keeps the shipped VARCHAR shape.
from app.models.calendar_binding import (
    CalendarAuthKind,
    CalendarBindingStatus,
    CalendarDirection,
    CalendarLocalKind,
    CalendarRedaction,
    CalendarRunStatus,
    CalendarService,
    CalendarSlotKind,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


# revision identifiers, used by Alembic.
revision: str = "0009_calendar_bindings"
down_revision: str | Sequence[str] | None = "0008_extension_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIRECTIONS = ("push", "pull", "two-way")
_REDACTIONS = ("none", "neutral", "full")
_AUTH_KINDS = ("oauth2", "app-password", "token", "none")
_BINDING_STATES = (
    "unbound",
    "needs-auth",
    "needs-secret",
    "ok",
    "degraded",
    "failing",
    "stale",
)
_LOCAL_KINDS = ("examination", "clinical_event", "occurrence")
_SLOT_KINDS = ("oauth", "password", "token")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "calendar_bindings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "service", sa.Enum(CalendarService, native_enum=False, values_callable=_enum_values),
            server_default=sa.text("'calendar'"), nullable=False,
        ),
        sa.Column("auth_kind", sa.Enum(CalendarAuthKind, native_enum=False, values_callable=_enum_values), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("account_label", sa.String(length=255), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "direction", sa.Enum(CalendarDirection, native_enum=False, values_callable=_enum_values),
            server_default=sa.text("'push'"), nullable=False,
        ),
        sa.Column(
            "redaction", sa.Enum(CalendarRedaction, native_enum=False, values_callable=_enum_values),
            server_default=sa.text("'neutral'"), nullable=False,
        ),
        sa.Column(
            "enabled", sa.Boolean(),
            server_default=sa.text("true"), nullable=False,
        ),
        sa.Column(
            "status", sa.Enum(CalendarBindingStatus, native_enum=False, values_callable=_enum_values),
            server_default=sa.text("'unbound'"), nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(),
            server_default=sa.text("0"), nullable=False,
        ),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column(
            # One-writer-per-remote-calendar key (Rev 3 H4): the derived,
            # protocol-keyed remote calendar identity. Credential-free —
            # URLs enter sanitized (scheme+host+path).
            "remote_calendar_id",
            sa.String(length=320),
            nullable=True,
        ),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_by", sa.UUID(), nullable=True
        ),
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
            "service = 'calendar'",
            name=op.f("ck_calendar_bindings_service_calendar_only"),
        ),
        sa.CheckConstraint(
            f"auth_kind IN ({_in(_AUTH_KINDS)})",
            name=op.f("ck_calendar_bindings_auth_kind_vocabulary"),
        ),
        sa.CheckConstraint(
            f"direction IN ({_in(_DIRECTIONS)})",
            name=op.f("ck_calendar_bindings_direction_vocabulary"),
        ),
        sa.CheckConstraint(
            f"redaction IN ({_in(_REDACTIONS)})",
            name=op.f("ck_calendar_bindings_redaction_vocabulary"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(_BINDING_STATES)})",
            name=op.f("ck_calendar_bindings_status_vocabulary"),
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name=op.f("ck_calendar_bindings_consecutive_failures_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_calendar_bindings_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_calendar_bindings_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_calendar_bindings_patient_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_bindings")),
    )
    for name, columns in (
        ("ix_calendar_bindings_user_id", ["user_id"]),
        ("ix_calendar_bindings_patient_id", ["patient_id"]),
        ("ix_calendar_bindings_provider", ["provider"]),
        ("ix_calendar_bindings_created_at", ["created_at"]),
        ("ix_calendar_bindings_tenant_id", ["tenant_id"]),
        ("ix_calendar_bindings_tenant_user_patient", ["tenant_id", "user_id", "patient_id"]),
        ("ix_calendar_bindings_remote_calendar_id", ["remote_calendar_id"]),
        ("ix_calendar_bindings_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "calendar_bindings", columns, unique=False)
    # One writer per remote calendar (ADR-0052 §9 / Rev 3 H4): unique among
    # writable bindings; pull-only subscriptions may share a calendar.
    op.create_index(
        "uq_calendar_bindings_remote_calendar",
        "calendar_bindings",
        ["tenant_id", "patient_id", "provider", "remote_calendar_id"],
        unique=True,
        postgresql_where=sa.text(
            "remote_calendar_id IS NOT NULL AND direction IN ('push', 'two-way')"
        ),
        sqlite_where=sa.text(
            "remote_calendar_id IS NOT NULL AND direction IN ('push', 'two-way')"
        ),
    )

    op.create_table(
        "calendar_event_links",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("local_kind", sa.Enum(CalendarLocalKind, native_enum=False, values_callable=_enum_values), nullable=False),
        sa.Column("local_id", sa.UUID(), nullable=False),
        sa.Column("uid", sa.String(length=255), nullable=False),
        sa.Column("remote_id", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column(
            # E3: SEPARATE per-side content pins — providers normalize on
            # write, so a single shared hash reports a phantom change (a
            # permanent update loop) on every run.
            "local_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("remote_hash", sa.String(length=64), nullable=True),
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
            f"local_kind IN ({_in(_LOCAL_KINDS)})",
            name=op.f("ck_calendar_event_links_local_kind_vocabulary"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["calendar_bindings.id"],
            name=op.f("fk_calendar_event_links_binding_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_event_links")),
        sa.UniqueConstraint(
            "binding_id", "uid", name=op.f("uq_calendar_event_links_binding_uid")
        ),
    )
    for name, columns in (
        ("ix_calendar_event_links_binding_id", ["binding_id"]),
        ("ix_calendar_event_links_binding_local", ["binding_id", "local_id"]),
        ("ix_calendar_event_links_created_at", ["created_at"]),
        ("ix_calendar_event_links_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "calendar_event_links", columns, unique=False)

    op.create_table(
        "calendar_binding_credentials",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Enum(CalendarSlotKind, native_enum=False, values_callable=_enum_values), nullable=False),
        sa.Column("sealed_blob", sa.Text(), nullable=False),
        sa.Column("kid", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.String(length=255)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            f"kind IN ({_in(_SLOT_KINDS)})",
            name=op.f("ck_calendar_binding_credentials_slot_kind_vocabulary"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_calendar_binding_credentials_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["calendar_bindings.id"],
            name=op.f("fk_calendar_binding_credentials_binding_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_binding_credentials")),
        sa.UniqueConstraint(
            "binding_id",
            "kind",
            name=op.f("uq_calendar_binding_credentials_slot"),
        ),
    )
    for name, columns in (
        ("ix_calendar_binding_credentials_binding_id", ["binding_id"]),
        ("ix_calendar_binding_credentials_created_at", ["created_at"]),
        ("ix_calendar_binding_credentials_tenant_id", ["tenant_id"]),
        ("ix_calendar_binding_credentials_tenant_kid", ["tenant_id", "kid"]),
        ("ix_calendar_binding_credentials_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "calendar_binding_credentials", columns, unique=False)

    op.create_table(
        "calendar_sync_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Enum(CalendarRunStatus, native_enum=False, values_callable=_enum_values), server_default=sa.text("'ok'"), nullable=False),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=24), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.String(length=16),
            server_default=sa.text("'manual'"), nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('ok', 'failed')",  # == CalendarRunStatus
            name=op.f("ck_calendar_sync_logs_run_status_vocabulary"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_calendar_sync_logs_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["calendar_bindings.id"],
            name=op.f("fk_calendar_sync_logs_binding_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_sync_logs")),
    )
    for name, columns in (
        ("ix_calendar_sync_logs_binding_id", ["binding_id"]),
        ("ix_calendar_sync_logs_binding_started", ["binding_id", "started_at"]),
        ("ix_calendar_sync_logs_created_at", ["created_at"]),
        ("ix_calendar_sync_logs_tenant_id", ["tenant_id"]),
        ("ix_calendar_sync_logs_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "calendar_sync_logs", columns, unique=False)

    # C3 dedup law (ADR-0052 §6): a pulled calendar event dedups on
    # ``(tenant_id, patient_id, external_id)``. The integration framework's
    # ``source_integration_id``-based index cannot be reused — a calendar
    # binding is deliberately NOT a ``UserIntegration`` (plan 52 §3) — so
    # this partial index mirrors its shape for calendar-sourced rows only
    # (``source_integration_id IS NULL``); UI rows (``external_id NULL``)
    # and integration rows bypass it.
    op.create_index(
        "uq_clinical_events_calendar_dedup",
        "clinical_events",
        ["tenant_id", "patient_id", "external_id"],
        unique=True,
        postgresql_where=sa.text(
            "external_id IS NOT NULL AND source_integration_id IS NULL"
        ),
        sqlite_where=sa.text(
            "external_id IS NOT NULL AND source_integration_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_clinical_events_calendar_dedup", table_name="clinical_events")
    op.drop_index(
        "uq_calendar_bindings_remote_calendar", table_name="calendar_bindings"
    )
    op.drop_table("calendar_sync_logs")
    op.drop_table("calendar_binding_credentials")
    op.drop_table("calendar_event_links")
    op.drop_table("calendar_bindings")

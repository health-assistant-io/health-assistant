"""Extension source lane: the imported-extension persistence (plan 7).

Four tables for the T2/T3 import lane (ADR-0040 persistence; parent plan
constitution rules 5, 6, 7a, 10):

**``extension_sources``** — one registered origin of imported content:
``kind`` (``git|file|registry``), ``uri``, the pinned ``ref_spec``, the
secret-slot reference (``auth_secret_ref`` — never a value), ``trust_tier``
(``T2|T3``), ``enabled`` and the fetch-failure columns. ``tenant_id`` is
nullable (``ON DELETE CASCADE``): NULL is a system-level source — the
genuinely mixed-scope case.

**``extension_source_versions``** — the **immutable** resolved content:
``source_id`` (FK cascade), ``commit_sha``/``digest`` (the content digest —
the consent anchor), ``manifest_digest``, the ``envelope`` JSONB,
``contract_version`` and the ``status`` state machine
(``pending|approved|rejected|revoked``). A digest change is a NEW row
requiring re-approval — content is never updated in place; a rejected
version keeps its typed registration problems in ``reason`` (plan 37 6.1 —
self-disable is a row, never a log-and-skip). Unique per ``(source_id,
digest)``.

**``extension_consents``** — who allowed what, version-bound: one row per
``(user, version, capability)`` with ``decision``
(``granted|denied``), the optional ``scope_json`` payload and
``decided_at``/``decided_by``. Consent binds to a manifest DIGEST via
``version_id`` so an upgraded extension re-prompts (ADR-0040 consent law).

**``extension_isolation_posture``** — runtime evidence per (version, host
profile): the source's ``requested_tier`` next to ``derived_tier``
(``contract.derive_tier`` is the one derivation) and the ``posture`` JSONB
(the graded containment result from DETECTION). ``operational`` — bounded
retention.

Downgrade drops all four tables and restores the previous shape; no data
backfill is attempted (owner doctrine — this program has no users).

Revision ID: 0008_extension_sources
Revises: 0007_data_lifecycle
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_extension_sources"
down_revision: str | Sequence[str] | None = "0007_data_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_KINDS = ("git", "file", "registry")
_SOURCE_TRUST_TIERS = ("T2", "T3")
_TIER_LADDER = ("T0", "T1", "T2", "T3")
_VERSION_STATUSES = ("pending", "approved", "rejected", "revoked")
_CONSENT_DECISIONS = ("granted", "denied")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "extension_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("ref_spec", sa.String(length=256), nullable=True),
        sa.Column("auth_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("trust_tier", sa.String(length=8), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            f"kind IN ({_in(_SOURCE_KINDS)})",
            name=op.f("ck_extension_sources_extension_source_kind"),
        ),
        sa.CheckConstraint(
            f"trust_tier IN ({_in(_SOURCE_TRUST_TIERS)})",
            name=op.f("ck_extension_sources_extension_source_trust_tier"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_extension_sources_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extension_sources")),
    )
    for name in (
        "ix_extension_sources_created_at",
        "ix_extension_sources_tenant_id",
        "ix_extension_sources_updated_at",
    ):
        op.create_index(name, "extension_sources", [name.removeprefix("ix_extension_sources_")])

    op.create_table(
        "extension_source_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "envelope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
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
            f"status IN ({_in(_VERSION_STATUSES)})",
            name=op.f("ck_extension_source_versions_extension_version_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["extension_sources.id"],
            name=op.f("fk_extension_source_versions_source_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extension_source_versions")),
        sa.UniqueConstraint(
            "source_id", "digest", name=op.f("uq_extension_source_versions_source_digest")
        ),
    )
    for name, columns in (
        ("ix_extension_source_versions_created_at", ["created_at"]),
        ("ix_extension_source_versions_source_id", ["source_id"]),
        ("ix_extension_source_versions_status", ["status"]),
        ("ix_extension_source_versions_updated_at", ["updated_at"]),
    ):
        op.create_index(
            name,
            "extension_source_versions",
            columns,
            unique=False,
        )

    op.create_table(
        "extension_consents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "scope_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by", sa.UUID(), nullable=True),
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
            "capability <> ''", name=op.f("ck_extension_consents_capability_not_empty")
        ),
        sa.CheckConstraint(
            f"decision IN ({_in(_CONSENT_DECISIONS)})",
            name=op.f("ck_extension_consents_extension_consent_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_extension_consents_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_extension_consents_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["extension_source_versions.id"],
            name=op.f("fk_extension_consents_version_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extension_consents")),
        sa.UniqueConstraint(
            "user_id",
            "version_id",
            "capability",
            name=op.f("uq_extension_consents_user_version_cap"),
        ),
    )
    for name, columns in (
        ("ix_extension_consents_created_at", ["created_at"]),
        ("ix_extension_consents_tenant_id", ["tenant_id"]),
        ("ix_extension_consents_updated_at", ["updated_at"]),
        ("ix_extension_consents_user_id", ["user_id"]),
        ("ix_extension_consents_version_id", ["version_id"]),
    ):
        op.create_index(name, "extension_consents", columns, unique=False)

    op.create_table(
        "extension_isolation_posture",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("host_profile", sa.String(length=64), nullable=False),
        sa.Column("requested_tier", sa.String(length=8), nullable=False),
        sa.Column("derived_tier", sa.String(length=8), nullable=False),
        sa.Column(
            "posture",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "probed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            f"requested_tier IN ({_in(_TIER_LADDER)})",
            name=op.f("ck_extension_isolation_posture_extension_posture_requested_tier"),
        ),
        sa.CheckConstraint(
            f"derived_tier IN ({_in(_TIER_LADDER)})",
            name=op.f("ck_extension_isolation_posture_extension_posture_derived_tier"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["extension_source_versions.id"],
            name=op.f("fk_extension_isolation_posture_version_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extension_isolation_posture")),
        sa.UniqueConstraint(
            "version_id",
            "host_profile",
            name=op.f("uq_extension_isolation_posture_version_host"),
        ),
    )
    for name, columns in (
        ("ix_extension_isolation_posture_created_at", ["created_at"]),
        ("ix_extension_isolation_posture_updated_at", ["updated_at"]),
        ("ix_extension_isolation_posture_version_id", ["version_id"]),
    ):
        op.create_index(name, "extension_isolation_posture", columns, unique=False)


def downgrade() -> None:
    op.drop_table("extension_isolation_posture")
    op.drop_table("extension_consents")
    op.drop_table("extension_source_versions")
    op.drop_table("extension_sources")

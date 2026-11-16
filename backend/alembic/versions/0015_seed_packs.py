"""Seed pack lane: sources, versions, runs, provenance, deferred refs.

Six tables for the data-only seed-pack lane (ADR 0056 — program Phase 9A;
parent plan constitution rules 5, 6, 7a, 10):

**``seed_sources``** — one registered origin of seed packs: ``kind``
(``bundled|file|git|integration|registry``), ``uri``, the pinned
``ref_spec``/``subpath``, the precedence ``priority`` (bundled 0 < curated
remote 100 < tenant overlay 200+) and ``scope`` (``system|tenant`` — a
tenant source always carries a tenant, a system source never does: CHECK).
``auth_secret_ref`` names a secret SLOT (never a value). ``tenant_id`` is
nullable (``ON DELETE CASCADE``): NULL is a system-level source — the
genuinely mixed-scope case. ``current_version_id`` points at the approved
version (added after ``seed_source_versions`` exists; ``ON DELETE SET NULL``).

**``seed_source_versions``** — the **immutable** digest-pinned packs:
``source_id`` (FK cascade), ``commit_sha``/``etag`` (the transport pin that
resolved the version), ``digest`` (the whole-pack content digest — the
approval anchor), the ``manifest`` JSONB, ``contract_version`` (the pack
schema version) and the ``status`` state machine
(``pending|approved|rejected|revoked``). A digest change is a NEW row
requiring re-approval — content is never updated in place; a rejected
version keeps its typed validation problems in ``reason``. Unique per
``(source_id, digest)``.

**``seed_import_runs``** — the plan/apply history per version: ``mode``
(``plan|apply``), ``status`` (``running|ok|failed|partial|conflicted``), the
``plan`` diff document, ``stats``, ``error`` and ``started_at``/``finished_at``.

**``seed_import_run_items``** — the per-item outcome of a run: ``action``
(``create|update|noop|retire|conflict|override|skip``), ``content_hash`` and
the ``conflict`` JSONB payload of a losing/overridden side — the durable
history the single provenance row cannot hold (ADR 0056 D4).

**``seed_item_provenance``** — one row per seeded catalog item,
``(entity_type, entity_id)`` unique: the ``source_version_id`` that last
wrote it (``SET NULL`` — provenance survives a removed source), its
``seed_key``, the applied ``content_hash``, the lifecycle ``state``
(``active|retired|overridden``) and ``user_modified`` (a user edit freezes
auto-updates; retire-not-delete keeps history, ADR 0056 D3/D5).

**``seed_deferred_refs``** — cross-pack references that could not resolve
at apply time: the typed ``payload`` to retry, ``attempts``/``last_error``
and the ``status`` (``pending`` rows are retried on every new approved
version — never silently skipped, ADR 0056 D5/D10).

Downgrade drops the ``current_version_id`` FK and then all six tables in
reverse dependency order; no data backfill is attempted (owner doctrine —
this program has no users).

Revision ID: 0015_seed_packs
Revises: 0014_audit_chain
Create Date: 2026-11-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_seed_packs"
down_revision: str | Sequence[str] | None = "0014_audit_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_KINDS = ("bundled", "file", "git", "integration", "registry")
_SOURCE_SCOPES = ("system", "tenant")
_VERSION_STATUSES = ("pending", "approved", "rejected", "revoked")
_RUN_MODES = ("plan", "apply")
_RUN_STATUSES = ("running", "ok", "failed", "partial", "conflicted")
_ITEM_ACTIONS = ("create", "update", "noop", "retire", "conflict", "override", "skip")
_ITEM_STATES = ("active", "retired", "overridden")
_DEFERRED_STATUSES = ("pending", "landed", "abandoned")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def _drop_indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.drop_index(f"ix_{table}_{column}", table_name=table)


def upgrade() -> None:
    op.create_table(
        "seed_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("ref_spec", sa.String(length=256), nullable=True),
        sa.Column("subpath", sa.String(length=512), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scope", sa.String(length=8), server_default=sa.text("'system'"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("auth_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
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
            f"kind IN ({_in(_SOURCE_KINDS)})", name=op.f("ck_seed_sources_seed_source_kind")
        ),
        sa.CheckConstraint(
            f"scope IN ({_in(_SOURCE_SCOPES)})", name=op.f("ck_seed_sources_seed_source_scope")
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 999", name=op.f("ck_seed_sources_priority_range")
        ),
        sa.CheckConstraint(
            "(scope = 'system' AND tenant_id IS NULL) OR "
            "(scope = 'tenant' AND tenant_id IS NOT NULL)",
            name=op.f("ck_seed_sources_scope_tenant_match"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_seed_sources_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_sources")),
    )
    _indexes("seed_sources", ["tenant_id", "created_at", "updated_at"])

    op.create_table(
        "seed_source_versions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("ck_seed_source_versions_seed_version_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["seed_sources.id"],
            name=op.f("fk_seed_source_versions_source_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_source_versions")),
        sa.UniqueConstraint(
            "source_id", "digest", name=op.f("uq_seed_source_versions_source_digest")
        ),
    )
    _indexes("seed_source_versions", ["source_id", "status", "created_at", "updated_at"])

    op.create_table(
        "seed_import_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            f"mode IN ({_in(_RUN_MODES)})", name=op.f("ck_seed_import_runs_seed_run_mode")
        ),
        sa.CheckConstraint(
            f"status IN ({_in(_RUN_STATUSES)})", name=op.f("ck_seed_import_runs_seed_run_status")
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["seed_source_versions.id"],
            name=op.f("fk_seed_import_runs_source_version_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_import_runs")),
    )
    _indexes("seed_import_runs", ["source_version_id", "status", "created_at", "updated_at"])

    op.create_table(
        "seed_import_run_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("seed_key", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("conflict", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            f"action IN ({_in(_ITEM_ACTIONS)})",
            name=op.f("ck_seed_import_run_items_seed_item_action"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["seed_import_runs.id"],
            name=op.f("fk_seed_import_run_items_run_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_import_run_items")),
    )
    _indexes("seed_import_run_items", ["run_id", "created_at", "updated_at"])

    op.create_table(
        "seed_item_provenance",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=True),
        sa.Column("seed_key", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "state", sa.String(length=16), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("user_modified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "applied_at",
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
            f"state IN ({_in(_ITEM_STATES)})",
            name=op.f("ck_seed_item_provenance_seed_item_state"),
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["seed_source_versions.id"],
            name=op.f("fk_seed_item_provenance_source_version_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_item_provenance")),
        sa.UniqueConstraint(
            "entity_type", "entity_id", name=op.f("uq_seed_item_provenance_entity")
        ),
    )
    _indexes(
        "seed_item_provenance",
        ["source_version_id", "seed_key", "state", "created_at", "updated_at"],
    )

    op.create_table(
        "seed_deferred_refs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
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
            f"status IN ({_in(_DEFERRED_STATUSES)})",
            name=op.f("ck_seed_deferred_refs_seed_deferred_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["seed_source_versions.id"],
            name=op.f("fk_seed_deferred_refs_source_version_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seed_deferred_refs")),
    )
    _indexes("seed_deferred_refs", ["source_version_id", "status", "created_at", "updated_at"])

    # The approved-version pointer closes the sources↔versions cycle: both
    # tables exist by now, so the FK lands as a plain ALTER.
    op.create_foreign_key(
        "fk_seed_sources_current_version_id",
        "seed_sources",
        "seed_source_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_seed_sources_current_version_id", "seed_sources", type_="foreignkey")
    _drop_indexes("seed_deferred_refs", ["source_version_id", "status", "created_at", "updated_at"])
    op.drop_table("seed_deferred_refs")
    _drop_indexes(
        "seed_item_provenance",
        ["source_version_id", "seed_key", "state", "created_at", "updated_at"],
    )
    op.drop_table("seed_item_provenance")
    _drop_indexes("seed_import_run_items", ["run_id", "created_at", "updated_at"])
    op.drop_table("seed_import_run_items")
    _drop_indexes("seed_import_runs", ["source_version_id", "status", "created_at", "updated_at"])
    op.drop_table("seed_import_runs")
    _drop_indexes("seed_source_versions", ["source_id", "status", "created_at", "updated_at"])
    op.drop_table("seed_source_versions")
    _drop_indexes("seed_sources", ["tenant_id", "created_at", "updated_at"])
    op.drop_table("seed_sources")

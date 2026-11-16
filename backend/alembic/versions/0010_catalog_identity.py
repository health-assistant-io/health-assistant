"""Catalog identity: seed keys, code attributes, scope laws, edge dedup (ADR 0055).

Program Phase 2 (plan A Phase 1 / ADR 0055 — UUID identity, immutable
``seed_key``, codes as attributes, display-only slug):

* ``seed_key String(120) NULL`` on ``medication_catalog``, ``allergy_catalog``,
  ``vaccine_catalog``, ``biomarker_definitions`` and ``concepts`` — the
  immutable natural key of a curated row. NULL for user-created rows, which
  stay outside the curated identity. Uniqueness is **per tenant scope**:
  ``UNIQUE (COALESCE(tenant_id, sentinel), seed_key) WHERE seed_key IS NOT
  NULL`` — system rows and a tenant overlay may share a key (seed-pack
  precedence, ADR-0056), two rows in the same scope may not. The sentinel is
  the shared all-zero UUID constant
  (:data:`app.core.catalog_identity.NULL_TENANT_SENTINEL`), the same literal
  ``0001_baseline`` uses for the per-tenant slug/MRN uniques.
* ``coding_system String(50)`` + ``code String(100)`` on ``medication_catalog``
  and ``allergy_catalog`` (RxNorm/ATC, SNOMED) — attributes, not identity,
  with a per-tenant-scope partial unique
  ``(COALESCE(tenant_id, sentinel), coding_system, code)`` where both are
  not null (dedupe hint, never the join key).
* ``ix_vaccine_catalog_slug_tenant`` — the vaccine ``slug`` joins the
  ``_PER_TENANT_UNIQUES`` semantics from ``0001_baseline`` (it was NOT NULL
  but unindexed); it stays display-only. A pre-0010 database holding two
  vaccines with the same slug in one tenant scope fails the index creation
  right here — deliberately: the program has no users yet, so the upgrade
  fails loudly instead of rewriting catalog data silently.
* Scope CHECKs per catalog table (+ ``concepts``), added ``NOT VALID`` and
  then ``VALIDATE``d so bad existing rows fail loudly instead of silently:
  ``scope='system' ⇒ tenant_id IS NULL``, ``scope IN ('tenant','user') ⇒
  tenant_id IS NOT NULL``, ``scope='user' ⇒ created_by IS NOT NULL``.
  Mirrored as ``CheckConstraint``s on the models. A pre-existing
  scope-violating row aborts the ``VALIDATE CONSTRAINT`` the same way
  (fail-loud; the whole revision rolls back and the database stays on
  ``0009``), and a duplicate ``seed_key`` / code within one scope aborts
  the matching unique index creation above.
* ``concept_edges``: the blanket ``ix_concept_edges_unique`` (one row per
  endpoints+relation+tenant across ALL statuses) becomes
  ``ix_concept_edges_approved_unique`` — unique per scope only where
  ``status = 'approved'``. Proposed (HITL-pending) and rejected (audit)
  rows may repeat, and they may repeat an approved row; the graph reads
  approved rows only. Existing approved duplicates are deduped (earliest
  row kept) before the index is created.

Downgrade removes the columns, CHECKs and new indexes and restores the
blanket edge uniqueness — collapsing every edge identity (scope + endpoints
+ relation) to its earliest row first, since head allows approved+proposed
coexistence and non-approved repeats the blanket index rejects. That
collapse is the one data-affecting step; the number of collapsed rows is
logged (``alembic.runtime.migration``) so operators can see it.

Revision ID: 0010_catalog_identity
Revises: 0009_calendar_bindings
Create Date: 2026-11-09

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.catalog_identity import NULL_TENANT_SENTINEL

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = "0010_catalog_identity"
down_revision: str | Sequence[str] | None = "0009_calendar_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_KEY_TABLES = (
    "medication_catalog",
    "allergy_catalog",
    "vaccine_catalog",
    "biomarker_definitions",
    "concepts",
)

_CODE_TABLES = (
    "medication_catalog",
    "allergy_catalog",
)

_SCOPE_TABLES = (
    "medication_catalog",
    "allergy_catalog",
    "vaccine_catalog",
    "biomarker_definitions",
    "concepts",
)

_SCOPE_CHECKS = (
    ("scope_system_no_tenant", "scope <> 'system' OR tenant_id IS NULL"),
    # Expressed as <> comparisons, not NOT IN: Postgres renders NOT IN as
    # <> ANY (ARRAY[...]) and the enum/CHECK parity gate reads every
    # ARRAY[...] body as a closed vocabulary (it is not one — it is a scope
    # invariant over two members of one).
    ("scope_scoped_has_tenant", "(scope <> 'tenant' AND scope <> 'user') OR tenant_id IS NOT NULL"),
    ("scope_user_has_creator", "scope <> 'user' OR created_by IS NOT NULL"),
)


def _coalesce(alias: str | None = None) -> str:
    """The tenant-scope expression, optionally alias-qualified for a self-join."""
    column = f"{alias}.tenant_id" if alias else "tenant_id"
    return f"COALESCE({column}, '{NULL_TENANT_SENTINEL}'::uuid)"


def _seed_key_indexes() -> list[tuple[str, str]]:
    return [
        (
            f"ix_{table}_seed_key_tenant",
            f"CREATE UNIQUE INDEX ix_{table}_seed_key_tenant ON {table} "
            f"({_coalesce()}, seed_key) WHERE seed_key IS NOT NULL",
        )
        for table in _SEED_KEY_TABLES
    ]


def _code_indexes() -> list[tuple[str, str]]:
    return [
        (
            f"ix_{table}_code_tenant",
            f"CREATE UNIQUE INDEX ix_{table}_code_tenant ON {table} "
            f"({_coalesce()}, coding_system, code) "
            "WHERE coding_system IS NOT NULL AND code IS NOT NULL",
        )
        for table in _CODE_TABLES
    ]


def _dedupe_edges(status_predicate: str | None = None) -> int:
    """Delete repeated edges (keep the earliest row per scope + identity).

    Returns the number of rows deleted.
    """
    conditions = [
        "a.src_type = b.src_type",
        "a.src_id = b.src_id",
        "a.dst_type = b.dst_type",
        "a.dst_id = b.dst_id",
        "a.relation = b.relation",
        f"{_coalesce('a')} = {_coalesce('b')}",
        "(a.created_at, a.id) > (b.created_at, b.id)",
    ]
    if status_predicate:
        conditions.append(f"a.status {status_predicate}")
        conditions.append(f"b.status {status_predicate}")
    statement = sa.text(
        f"DELETE FROM concept_edges a USING concept_edges b WHERE {' AND '.join(conditions)}"
    )
    return op.get_bind().execute(statement).rowcount


def upgrade() -> None:
    """Add identity columns, per-tenant-scope uniqueness, scope laws, edge dedup."""
    for table in _SEED_KEY_TABLES:
        op.add_column(table, sa.Column("seed_key", sa.String(length=120), nullable=True))
    for table in _CODE_TABLES:
        op.add_column(table, sa.Column("coding_system", sa.String(length=50), nullable=True))
        op.add_column(table, sa.Column("code", sa.String(length=100), nullable=True))

    # Approved-edge dedup before the unique index; non-approved repeats stay —
    # only the approved set (the graph's read set) must be unique. IF EXISTS:
    # a database that predates the blanket index (the dedup guard's whole
    # reason) has nothing to drop — the guard must stay reachable there.
    _dedupe_edges("= 'approved'")

    op.execute("DROP INDEX IF EXISTS ix_concept_edges_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_concept_edges_approved_unique ON concept_edges "
        f"({_coalesce()}, src_type, src_id, relation, dst_type, dst_id) "
        "WHERE status = 'approved'"
    )
    for _name, statement in [*_seed_key_indexes(), *_code_indexes()]:
        op.execute(statement)
    op.execute(
        "CREATE UNIQUE INDEX ix_vaccine_catalog_slug_tenant ON vaccine_catalog "
        f"(slug, {_coalesce()})"
    )

    for table in _SCOPE_TABLES:
        for name, expression in _SCOPE_CHECKS:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{name} "
                f"CHECK ({expression}) NOT VALID"
            )
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT ck_{table}_{name}")


def downgrade() -> None:
    """Restore the pre-identity shape (columns, indexes, scope CHECKs)."""
    for table in _SCOPE_TABLES:
        for name, _expression in _SCOPE_CHECKS:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_{name}")

    op.drop_index("ix_vaccine_catalog_slug_tenant", table_name="vaccine_catalog")
    for name, _statement in [*_code_indexes(), *_seed_key_indexes()]:
        op.drop_index(name, table_name=_table_of(name))
    op.drop_index("ix_concept_edges_approved_unique", table_name="concept_edges")

    # The restored blanket unique index rejects everything head allowed
    # beyond one row per identity (approved+proposed coexisting, non-approved
    # repeats); collapse each identity to its earliest row so the downgrade is
    # deterministic instead of failing midway.
    collapsed = _dedupe_edges()
    logger.info(
        "0010 downgrade: collapsed %d duplicate concept edge row(s) to the "
        "earliest per identity to restore blanket uniqueness (data-affecting "
        "step; the collapsed rows are deleted)",
        collapsed,
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_concept_edges_unique ON concept_edges "
        f"(src_type, src_id, dst_type, dst_id, relation, {_coalesce()})"
    )

    for table in _CODE_TABLES:
        op.drop_column(table, "code")
        op.drop_column(table, "coding_system")
    for table in _SEED_KEY_TABLES:
        op.drop_column(table, "seed_key")


def _table_of(index_name: str) -> str:
    """The table a generated ``ix_<table>_<rest>`` index belongs to."""
    for table in (*_CODE_TABLES, *_SEED_KEY_TABLES):
        if index_name.startswith(f"ix_{table}_"):
            return table
    raise ValueError(f"unmapped index {index_name}")

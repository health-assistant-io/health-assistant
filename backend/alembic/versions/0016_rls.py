"""Row-level security on every tenant-scoped table (ADR 0054 D4/D6, phase 11).

The second wall: policies read transaction-local GUCs planted by
``app.core.tenant_context`` (``app.tenant_id`` / ``app.system`` planted at
every transaction begin by the engine's ``after_begin`` hook). Missing or
reset GUCs read as empty string/NULL — every policy fails closed.

Coverage, derived from the models (model-first — the inventory test
``tests/test_rls_row_level_security.py`` re-derives the same classification
from the raw catalog):

1. **NOT NULL ``tenant_id``** — one ``FOR ALL`` policy
   ``tenant_id = <ctx tenant> OR <system>`` (read + write).
2. **Nullable ``tenant_id``**, system-rows-visible flavor (catalogs,
   broadcasts, instance-level AI config tenants must read) —
   ``rls_tenant_read``: ``tenant_id IS NULL OR tenant_id = <ctx> OR <system>``;
   ``rls_tenant_write``: tenant rows only for tenant contexts, NULL rows
   only under the system flag (a tenant can never mint a system row).
3. **Nullable ``tenant_id``**, system-rows-hidden flavor (audit chains,
   system ops logs, lifecycle ledger) — the tenant match or the system
   flag: NULL rows stay hidden from plain tenants, while the system actor
   has **full reach** (every tenant's rows plus the system chain) — the
   cross-tenant operator surfaces (SYSTEM_ADMIN audit viewer/export,
   the chain verifier's ``?tenant_id=<foreign>`` walk, the cross-tenant
   erasure ledger, AI usage stats) run on it.
4. **Tenant-derived child tables** (no ``tenant_id`` column, one FK hop to
   a covered parent) — ``EXISTS (SELECT 1 FROM <parent> WHERE
   <parent>.id = <child>.<fk>)``; the parent's own policy enforces inside
   the subquery, so chains compose (``seed_deferred_refs`` → versions →
   sources).

Documented exceptions (no policy):

- ``telemetry_data`` — TimescaleDB hypertable with columnstore compression;
  ``ENABLE ROW LEVEL SECURITY`` raises ``FeatureNotSupported`` on compressed
  hypertables (Timescale 2.x), and the compression is a deliberate storage
  feature (0005). The raw table stays granted to the runtime role for
  **writes, erasure and retention** (every one of those paths carries its
  own service-layer tenant filter); every *aggregate read* goes through a
  GUC-filtered scoped view: the trends live-aggregate path reads
  ``telemetry_live_scoped`` (below), never the raw hypertable. Revisit if
  Timescale lifts the restriction.
- ``telemetry_hourly`` / ``telemetry_daily`` / ``telemetry_monthly``
  continuous aggregates read the internal materialization, which RLS never
  covers — the raw caggs are REVOKEd from the app role and the
  ``telemetry_*_scoped`` views (GUC filter inside the view definition,
  unpickable by the runtime role) are the only granted surface.
- Global/instance tables without tenancy: ``tenants`` (the registry),
  ``users``-anchored ``tenants.owner_id`` aside, ``ai_task_defs``,
  ``anatomy_figures``, ``biomarker_states``, ``instance_settings``,
  ``proposal_kinds``, ``retention_policies``, ``system_integrations``,
  ``system_settings``, ``units``, ``alembic_version`` (also REVOKEd — the
  runtime role never touches the version row).
- Runtime-managed langgraph checkpoint tables (outside the migration lane;
  no tenant columns).

FORCE ROW LEVEL SECURITY is deliberately **not** set: the table owner is
the migration role (``neuronection_health_owner``) and alembic data
migrations must see cross-tenant rows; the runtime role
(``neuronection_health_app``) owns no table (pinned by test) so RLS applies
to everything it does. The upgrade also re-asserts the app role's DML
grants + default privileges (self-healing for volumes created before the
role split) and revokes ``alembic_version`` + the raw caggs from it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_rls"
down_revision: str | Sequence[str] | None = "0015_seed_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"

_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"

#: NOT NULL tenant tables + the write half of every nullable flavor.
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"
#: Nullable tables whose NULL-tenant (system) rows every tenant may read.
VISIBLE_READ = f"tenant_id IS NULL OR {TENANT_ONLY}"
#: Nullable tables whose NULL-tenant rows stay hidden from plain tenants.
#: Identical to :data:`TENANT_ONLY` **by decision** (ADR 0054 phase 11
#: revision): the system flag reaches *every* row, not only the NULL ones —
#: the SYSTEM_ADMIN operator surfaces (audit viewer/export, the chain
#: verifier's foreign-tenant walk, the cross-tenant erasure ledger, AI
#: usage) must see tenant B's rows from the system context, and a system
#: context is planted only by the auth layer for the SYSTEM_ADMIN role and
#: by service jobs, never from request data. The flavor still differs from
#: :data:`VISIBLE_READ`: a plain tenant never sees the NULL rows.
HIDDEN_READ = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"

#: Nullable ``tenant_id`` tables that mix system (NULL) rows with tenant
#: rows, and the flavor each keeps. Anything absent from both maps with a
#: tenant column is NOT NULL and gets :data:`TENANT_ONLY` directly.
SYSTEM_ROWS_VISIBLE = {
    "ai_providers",
    "ai_task_assignments",
    "allergy_catalog",
    "anatomy_structures",
    "biomarker_definitions",
    "clinical_event_types",
    "concept_edges",
    "concepts",
    "doctors",
    "extension_sources",
    "medication_catalog",
    "notification_deliveries",
    "notification_recipients",
    "notification_triggers",
    "notifications",
    "vaccine_catalog",
}
SYSTEM_ROWS_HIDDEN = {
    "ai_call_logs",
    "audit_chain_heads",
    "audit_events",
    "catalog_audit_log",
    "erasure_requests",
    "seed_sources",
    "task_logs",
}

#: Tenant-derived children (no ``tenant_id`` column): table -> FK column of
#: the parent whose policy anchors them. Chains compose — a parent may
#: itself be a covered child (``extension_isolation_posture`` →
#: ``extension_source_versions`` → ``extension_sources``).
PARENT_ANCHORS = {
    "ai_models": ("ai_providers", "provider_id"),
    "auth_sessions": ("users", "user_id"),
    "biomarker_allowed_states": ("biomarker_definitions", "biomarker_id"),
    "biomarker_reference_ranges": ("biomarker_definitions", "biomarker_id"),
    "calendar_event_links": ("calendar_bindings", "binding_id"),
    "chat_messages": ("chat_sessions", "session_id"),
    "clinical_event_occurrences": ("clinical_events", "event_id"),
    "concept_kind_tags": ("concepts", "concept_id"),
    "event_anatomy_links": ("clinical_events", "event_id"),
    "event_examination_links": ("clinical_events", "event_id"),
    "event_observation_links": ("clinical_events", "event_id"),
    "examination_doctors": ("examinations", "examination_id"),
    "extension_isolation_posture": ("extension_source_versions", "version_id"),
    "extension_source_versions": ("extension_sources", "source_id"),
    "organization_doctors": ("fhir_organizations", "organization_id"),
    "seed_deferred_refs": ("seed_source_versions", "source_version_id"),
    "seed_import_run_items": ("seed_import_runs", "run_id"),
    "seed_import_runs": ("seed_source_versions", "source_version_id"),
    "seed_item_provenance": ("seed_source_versions", "source_version_id"),
    "seed_source_versions": ("seed_sources", "source_id"),
}

#: Continuous aggregates are not RLS-coverable; the app role reads the
#: GUC-filtered ``*_scoped`` views only.
CAGG_VIEWS = (
    ("telemetry_hourly_scoped", "telemetry_hourly"),
    ("telemetry_daily_scoped", "telemetry_daily"),
    ("telemetry_monthly_scoped", "telemetry_monthly"),
)

#: The raw hypertable cannot carry RLS (compression), so its aggregate
#: readers get a GUC-filtered scoped view too. The raw table stays granted
#: for writes, erasure and retention (their service-layer tenant filters
#: remain mandatory — RLS is the second wall).
LIVE_SCOPED_VIEW = ("telemetry_live_scoped", "telemetry_data")

#: Documented no-policy exceptions with a ``tenant_id`` column.
#: ``telemetry_data`` is a TimescaleDB hypertable with columnstore
#: compression — ``ENABLE ROW LEVEL SECURITY`` raises ``FeatureNotSupported``
#: on compressed hypertables (Timescale 2.x); see the module docs.
NO_POLICY_TABLES = frozenset({"telemetry_data"})

#: Tables created by a LATER revision whose models already sit in
#: ``Base.metadata`` when this revision runs: their policies are owned by
#: their own migration (``0017`` mirrors the SYSTEM_ROWS_HIDDEN flavor,
#: ``0018_enterprise_identity`` and ``0021_api_tokens`` use the
#: NOT-NULL-tenant ``TENANT_ONLY`` flavor), so this revision must not
#: classify or touch them (the tables do not exist here yet — upgrade or
#: downgrade).
POST_0016_TABLES = frozenset(
    {
        "access_elevations",
        "ai_price_overrides",
        "api_tokens",
        "integration_principals",
        "scim_group_members",
        "scim_groups",
        "scim_tokens",
        "tenant_sso_configs",
        "user_identities",
        "webauthn_credentials",
    }
)


def _has_timescaledb() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


def _role_exists(bind, role: str) -> bool:
    return (
        bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
        is not None
    )


def _tenant_scoped_tables() -> tuple[list[str], list[str]]:
    """(NOT NULL tenant tables, nullable tenant tables) from the models."""
    from app.models.base import Base

    not_null: list[str] = []
    nullable: list[str] = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        column = table.columns.get("tenant_id")
        if column is None or table.name in NO_POLICY_TABLES or table.name in POST_0016_TABLES:
            continue
        (nullable if column.nullable else not_null).append(table.name)
    return not_null, nullable


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    not_null_tables, nullable_tables = _tenant_scoped_tables()

    for table in not_null_tables:
        _enable_rls(table)
        op.execute(f"DROP POLICY IF EXISTS rls_tenant ON {table}")
        op.execute(
            f"CREATE POLICY rls_tenant ON {table} USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
        )

    for table in nullable_tables:
        if table not in (SYSTEM_ROWS_VISIBLE | SYSTEM_ROWS_HIDDEN):
            raise RuntimeError(
                f"Nullable-tenant table {table} is not in the flavor maps — "
                "classify it (system rows visible or hidden) before migrating."
            )
        _enable_rls(table)
        op.execute(f"DROP POLICY IF EXISTS rls_tenant_read ON {table}")
        op.execute(f"DROP POLICY IF EXISTS rls_tenant_write ON {table}")
        read_expr = VISIBLE_READ if table in SYSTEM_ROWS_VISIBLE else HIDDEN_READ
        op.execute(f"CREATE POLICY rls_tenant_read ON {table} FOR SELECT USING ({read_expr})")
        op.execute(
            f"CREATE POLICY rls_tenant_write ON {table} FOR INSERT WITH CHECK ({TENANT_ONLY})"
        )
        op.execute(
            f"CREATE POLICY rls_tenant_write_upd ON {table} FOR UPDATE "
            f"USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
        )
        op.execute(
            f"CREATE POLICY rls_tenant_write_del ON {table} FOR DELETE USING ({TENANT_ONLY})"
        )

    for table, (parent, fk_column) in PARENT_ANCHORS.items():
        _enable_rls(table)
        op.execute(f"DROP POLICY IF EXISTS rls_tenant_parent ON {table}")
        op.execute(
            f"CREATE POLICY rls_tenant_parent ON {table} "
            f"USING (EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{fk_column})) "
            f"WITH CHECK (EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{fk_column}))"
        )

    if _has_timescaledb():
        for scoped, raw in (*CAGG_VIEWS, LIVE_SCOPED_VIEW):
            op.execute(f"DROP VIEW IF EXISTS {scoped}")
            op.execute(f"CREATE VIEW {scoped} AS SELECT * FROM {raw} WHERE ({TENANT_ONLY})")

    bind = op.get_bind()
    if _role_exists(bind, APP_ROLE):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
        )
        # The runtime role never touches the version row or the raw caggs —
        # the GUC-filtered views are the only aggregate surface it gets.
        # (``telemetry_data`` itself stays granted: writes, erasure and
        # retention need it; its aggregate readers use ``telemetry_live_scoped``.)
        op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
        if _has_timescaledb():
            for _scoped, raw in CAGG_VIEWS:
                op.execute(f"REVOKE ALL ON {raw} FROM {APP_ROLE}")
                op.execute(f"GRANT SELECT ON {raw}_scoped TO {APP_ROLE}")
            op.execute(f"GRANT SELECT ON {LIVE_SCOPED_VIEW[0]} TO {APP_ROLE}")


def downgrade() -> None:
    not_null_tables, nullable_tables = _tenant_scoped_tables()

    if _has_timescaledb():
        for scoped, _raw in (*CAGG_VIEWS, LIVE_SCOPED_VIEW):
            op.execute(f"DROP VIEW IF EXISTS {scoped}")
        if _role_exists(op.get_bind(), APP_ROLE):
            for _scoped, raw in CAGG_VIEWS:
                op.execute(f"GRANT SELECT ON {raw} TO {APP_ROLE}")

    for table in not_null_tables:
        op.execute(f"DROP POLICY IF EXISTS rls_tenant ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in nullable_tables:
        for name in (
            "rls_tenant_read",
            "rls_tenant_write",
            "rls_tenant_write_upd",
            "rls_tenant_write_del",
        ):
            op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in PARENT_ANCHORS:
        op.execute(f"DROP POLICY IF EXISTS rls_tenant_parent ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

"""Row-level security behavior, run as the **app role** (ADR 0054 D4/D6).

The suite connects a dedicated pool as the runtime role
(``neuronection_health_app`` — the role the production containers use), so
owner bypass can never make a test vacuous. Fixture rows are seeded through
the default (owner) pool; every assertion about visibility runs through the
app-role pool with the context planted the way the engine hook plants it.

Pins:

* the raw-session inventory — every tenant-scoped table carries a policy,
  every exception is named with its reason, and the runtime role owns no
  table and cannot read ``alembic_version`` or the raw caggs;
* cross-tenant SELECT/INSERT/UPDATE/DELETE fail; an unset GUC answers zero
  rows; system rows are visible (or hidden) exactly where the flavor maps
  say; a tenant cannot mint or repoint system rows;
* the hidden-read revision — the system actor reaches every tenant's rows
  on audit-flavor tables (the SYSTEM_ADMIN audit viewer/export, the
  chain verifier's foreign-tenant walk, the cross-tenant erasure ledger
  and AI-usage stats), a switched SYSTEM_ADMIN keeps that reach while its
  GUC stays on the home tenant, and plain tenants still see only their own;
* the audit lane's required write succeeds as the runtime role end-to-end;
* tenant-derived children ride their parent's policy (chat messages,
  seed-pack chains);
* pool hygiene — interleaved sessions on the shared pool never bleed;
* the Timescale surface — ``telemetry_data`` is the documented exception
  (hypertable + columnstore), the ``telemetry_*_scoped`` views enforce the
  GUC filter and are the only granted aggregate surface;
* presign/facade/auth/audit/worker contexts plant the right GUCs;
* the policy expression evaluates once per query (InitPlan — index
  friendly), never per row — on a direct table and on a child-chain table
  (hashed parent EXISTS, GUC reads hoisted).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import tenant_context
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.tenant_context import tenant_scope

APP_ROLE = "neuronection_health_app"

#: Tables deliberately without a policy — each with its reason (the same
#: inventory the 0016 migration ships). The test fails when a new table
#: appears without either a policy or an entry here, and when the reasons
#: drift from reality.
NO_POLICY_EXCEPTIONS = {
    "alembic_version": "the migration version row — revoked from the app role",
    "anatomy_figures": "global figure catalog, no tenant column",
    "ai_task_defs": "global AI task vocabulary, no tenant column",
    "biomarker_states": "global state vocabulary, no tenant column",
    "checkpoint_blobs": "runtime-managed langgraph table, no tenant column",
    "checkpoint_migrations": "runtime-managed langgraph table, no tenant column",
    "checkpoint_writes": "runtime-managed langgraph table, no tenant column",
    "checkpoints": "runtime-managed langgraph table, no tenant column",
    "instance_settings": "instance-scope settings, no tenant column",
    "proposal_kinds": "family proposal-kind vocabulary, no tenant column",
    "retention_policies": "instance-scope retention windows, no tenant column",
    "system_integrations": "instance-scope integrations, no tenant column",
    "system_settings": "instance-scope settings, no tenant column",
    "telemetry_data": (
        "Timescale hypertable with columnstore compression — RLS is "
        "FeatureNotSupported on compressed hypertables; the raw table "
        "stays granted for writes, erasure and retention (service-layer "
        "tenant filters), every aggregate read goes through the "
        "GUC-filtered views (telemetry_*_scoped for the caggs, "
        "telemetry_live_scoped for the trends live path)"
    ),
    "tenants": "the tenant registry itself, no tenant column",
    "units": "global unit vocabulary, no tenant column",
}

CAGG_VIEWS = ("telemetry_hourly", "telemetry_daily", "telemetry_monthly")


def _app_url() -> str:
    """The runtime-role URL: same database, the APP secret (ADR-0064)."""
    from tests._db_roles import APP_ROLE, db_url

    return db_url(settings.POSTGRES_DB, role=APP_ROLE)


@pytest.fixture(scope="module")
def app_engine():
    """The runtime-role pool (function-recreated connections, NullPool).

    pytest-asyncio gives every test its own event loop; asyncpg transports
    are loop-bound, so the pool never keeps a connection across tests.
    """
    engine = create_async_engine(_app_url(), poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    tenant_context.install_session_hook(factory)
    yield factory
    import asyncio

    asyncio.run(engine.dispose())


@pytest_asyncio.fixture
async def app_session(app_engine):
    async with app_engine() as session:
        yield session


@pytest.fixture(scope="module")
def owner_sync_engine():
    engine = create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    yield engine
    engine.dispose()


async def _seed_tenant(*, name: str = "rls-tenant") -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": str(tenant_id), "name": name, "slug": f"rls-{tenant_id}"},
        )
        await session.commit()
    return tenant_id


async def _seed_user(
    tenant_id: uuid.UUID, email: str | None = None, *, role: str = "USER"
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, tenant_id, role, is_active) "
                "VALUES (:id, :email, :tenant_id, :role, true)"
            ),
            {
                "id": str(user_id),
                "email": email or f"rls-{user_id}@test.local",
                "tenant_id": str(tenant_id),
                "role": role,
            },
        )
        await session.commit()
    return user_id


async def _seed_document(
    tenant_id: uuid.UUID, owner_id: uuid.UUID, filename: str = "rls.txt"
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, tenant_id, owner_id, filename, file_path, "
                "status, include_in_extraction, is_edited) "
                "VALUES (:id, :tenant_id, :owner_id, :filename, '/tmp/rls-probe.txt', "
                "'uploaded', false, false)"
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "owner_id": str(owner_id),
                "filename": filename,
            },
        )
        await session.commit()
    return document_id


async def _document_count(session, document_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM documents WHERE id = :id"), {"id": str(document_id)}
    )
    return int(result.scalar_one())


async def _seed_api_token(tenant_id: uuid.UUID, owner_id: uuid.UUID) -> uuid.UUID:
    token_id = uuid.uuid4()
    prefix = token_id.hex[:12]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO api_tokens (id, tenant_id, owner_user_id, name, "
                "token_prefix, token_hash, permissions, patient_scope, patient_ids) "
                "VALUES (:id, :tenant_id, :owner_id, 'rls', :prefix, :hash, "
                "'[]'::jsonb, 'all', '[]'::jsonb)"
            ),
            {
                "id": str(token_id),
                "tenant_id": str(tenant_id),
                "owner_id": str(owner_id),
                "prefix": prefix,
                "hash": f"{token_id.hex}{token_id.hex[:32]}",
            },
        )
        await session.commit()
    return token_id


async def _api_token_count(session, token_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM api_tokens WHERE id = :id"), {"id": str(token_id)}
    )
    return int(result.scalar_one())


async def _seed_integration_principal(tenant_id: uuid.UUID, owner_id: uuid.UUID) -> uuid.UUID:
    principal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO integration_principals (id, tenant_id, owner_user_id, name, "
                "kind, scopes, patient_scope, patient_ids, status) "
                "VALUES (:id, :tenant_id, :owner_id, 'rls', 'service', "
                "'[]'::jsonb, 'none', '[]'::jsonb, 'active')"
            ),
            {
                "id": str(principal_id),
                "tenant_id": str(tenant_id),
                "owner_id": str(owner_id),
            },
        )
        await session.commit()
    return principal_id


async def _integration_principal_count(session, principal_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM integration_principals WHERE id = :id"),
        {"id": str(principal_id)},
    )
    return int(result.scalar_one())


async def _chat_thread(tenant_id: uuid.UUID, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A chat session + one message (tenant-derived child coverage)."""
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    user_id = await _seed_user(tenant_id)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO chat_sessions (id, tenant_id, user_id, title) "
                "VALUES (:id, :tenant_id, :user_id, :title)"
            ),
            {
                "id": str(session_id),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "title": title,
            },
        )
        await session.execute(
            text(
                "INSERT INTO chat_messages (id, session_id, role, content) "
                "VALUES (:id, :session_id, 'user', '{\"probe\": true}')"
            ),
            {"id": str(message_id), "session_id": str(session_id)},
        )
        await session.commit()
    return session_id, message_id


# ---------------------------------------------------------------------------
# Raw-session inventory
# ---------------------------------------------------------------------------


def test_inventory_every_tenant_table_has_a_policy(owner_sync_engine):
    """Every public table has RLS + a policy, or is a named exception."""
    from app.models.base import Base

    metadata_tables = set(Base.metadata.tables)
    with owner_sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, "
                "count(p.polname) FILTER (WHERE p.polname IS NOT NULL) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "GROUP BY c.relname, c.relrowsecurity"
            )
        ).all()
    db_tables = {row[0]: (bool(row[1]), int(row[2])) for row in rows}

    uncovered = []
    for name, (has_rls, policy_count) in sorted(db_tables.items()):
        if name in NO_POLICY_EXCEPTIONS:
            continue
        if name not in metadata_tables:
            continue
        if not has_rls or policy_count == 0:
            uncovered.append(name)
    assert not uncovered, f"tenant-scoped tables without a policy: {uncovered}"

    stale = sorted(set(NO_POLICY_EXCEPTIONS) - set(db_tables))
    assert not stale, f"exception inventory names nonexistent tables: {stale}"

    covered = {
        name
        for name, table in Base.metadata.tables.items()
        if table.columns.get("tenant_id") is not None and name != "telemetry_data"
    } | set(POLICY_CHILDREN)
    for name in covered:
        has_rls, policy_count = db_tables[name]
        assert has_rls and policy_count > 0, f"{name} lost its RLS coverage"


#: The tenant-derived children the migration covers through their parent.
POLICY_CHILDREN = frozenset(
    {
        "ai_models",
        "auth_sessions",
        "biomarker_allowed_states",
        "biomarker_reference_ranges",
        "calendar_event_links",
        "chat_messages",
        "clinical_event_occurrences",
        "concept_kind_tags",
        "event_anatomy_links",
        "event_examination_links",
        "event_observation_links",
        "examination_doctors",
        "extension_isolation_posture",
        "extension_source_versions",
        "organization_doctors",
        "seed_deferred_refs",
        "scim_group_members",
        "seed_import_run_items",
        "seed_import_runs",
        "seed_item_provenance",
        "seed_source_versions",
    }
)


def test_runtime_role_owns_no_table(owner_sync_engine):
    """Non-owner runtime role — the owner exemption never applies to it."""
    with owner_sync_engine.connect() as conn:
        owned = (
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "AND pg_get_userbyid(c.relowner) = :role"
                ),
                {"role": APP_ROLE},
            )
            .scalars()
            .all()
        )
    assert owned == [], f"the runtime role owns tables (RLS bypass!): {owned}"


def test_runtime_role_cannot_read_version_row_or_raw_caggs(owner_sync_engine):
    with owner_sync_engine.connect() as conn:
        assert not conn.execute(
            text("SELECT has_table_privilege(:role, 'alembic_version', 'SELECT')"),
            {"role": APP_ROLE},
        ).scalar()
        for view in CAGG_VIEWS:
            raw = conn.execute(
                text("SELECT has_table_privilege(:role, :v, 'SELECT')"),
                {"role": APP_ROLE, "v": view},
            ).scalar()
            scoped = conn.execute(
                text("SELECT has_table_privilege(:role, :v, 'SELECT')"),
                {"role": APP_ROLE, "v": f"{view}_scoped"},
            ).scalar()
            assert not raw, f"the app role still reads the raw cagg {view}"
            assert scoped, f"the app role lost the scoped view {view}_scoped"


# ---------------------------------------------------------------------------
# Cross-tenant isolation (documents — the PHI carrier)
# ---------------------------------------------------------------------------


async def test_cross_tenant_select_is_empty(app_session):
    tenant_a = await _seed_tenant(name="rls-a")
    tenant_b = await _seed_tenant(name="rls-b")
    await _seed_user(tenant_a)
    doc_b = await _seed_document(tenant_b, await _seed_user(tenant_b))

    with tenant_scope(tenant_a):
        async with app_session.begin():
            assert await _document_count(app_session, doc_b) == 0


async def test_same_tenant_select_succeeds(app_session):
    tenant_a = await _seed_tenant(name="rls-same")
    user_a = await _seed_user(tenant_a)
    doc_a = await _seed_document(tenant_a, user_a)

    with tenant_scope(tenant_a):
        async with app_session.begin():
            assert await _document_count(app_session, doc_a) == 1


async def test_api_tokens_are_tenant_isolated(app_session):
    """The credential table obeys the same second wall as PHI carriers."""
    tenant_a = await _seed_tenant(name="rls-token-a")
    tenant_b = await _seed_tenant(name="rls-token-b")
    token_b = await _seed_api_token(tenant_b, await _seed_user(tenant_b))

    with tenant_scope(tenant_a):
        async with app_session.begin():
            assert await _api_token_count(app_session, token_b) == 0

    with tenant_scope(tenant_b):
        async with app_session.begin():
            assert await _api_token_count(app_session, token_b) == 1

    tenant_context.clear()
    async with app_session.begin():
        assert await _api_token_count(app_session, token_b) == 0


async def test_cross_tenant_api_token_insert_fails(app_session):
    tenant_a = await _seed_tenant(name="rls-token-insert-a")
    tenant_b = await _seed_tenant(name="rls-token-insert-b")
    owner_a = await _seed_user(tenant_a)
    await _seed_api_token(tenant_b, await _seed_user(tenant_b))

    with pytest.raises(DBAPIError), tenant_scope(tenant_a):
        async with app_session.begin():
            await app_session.execute(
                text(
                    "INSERT INTO api_tokens (id, tenant_id, owner_user_id, name, "
                    "token_prefix, token_hash, permissions, patient_scope, patient_ids) "
                    "VALUES (:id, :tenant_id, :owner_id, 'evil', :prefix, :hash, "
                    "'[]'::jsonb, 'all', '[]'::jsonb)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_b),
                    "owner_id": str(owner_a),
                    "prefix": uuid.uuid4().hex[:12],
                    "hash": uuid.uuid4().hex + uuid.uuid4().hex[:32],
                },
            )


async def test_integration_principals_are_tenant_isolated(app_session):
    """Service identities obey the same second wall as every credential row."""
    tenant_a = await _seed_tenant(name="rls-principal-a")
    tenant_b = await _seed_tenant(name="rls-principal-b")
    principal_b = await _seed_integration_principal(tenant_b, await _seed_user(tenant_b))

    with tenant_scope(tenant_a):
        async with app_session.begin():
            assert await _integration_principal_count(app_session, principal_b) == 0

    with tenant_scope(tenant_b):
        async with app_session.begin():
            assert await _integration_principal_count(app_session, principal_b) == 1

    tenant_context.clear()
    async with app_session.begin():
        assert await _integration_principal_count(app_session, principal_b) == 0


async def test_cross_tenant_integration_principal_insert_fails(app_session):
    tenant_a = await _seed_tenant(name="rls-principal-insert-a")
    tenant_b = await _seed_tenant(name="rls-principal-insert-b")
    owner_a = await _seed_user(tenant_a)
    await _seed_integration_principal(tenant_b, await _seed_user(tenant_b))

    with pytest.raises(DBAPIError), tenant_scope(tenant_a):
        async with app_session.begin():
            await app_session.execute(
                text(
                    "INSERT INTO integration_principals (id, tenant_id, owner_user_id, "
                    "name, kind, scopes, patient_scope, patient_ids, status) "
                    "VALUES (:id, :tenant_id, :owner_id, 'evil', 'service', "
                    "'[]'::jsonb, 'none', '[]'::jsonb, 'active')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_b),
                    "owner_id": str(owner_a),
                },
            )


async def test_guc_unset_returns_zero_rows(app_session):
    tenant_a = await _seed_tenant(name="rls-unset")
    user_a = await _seed_user(tenant_a)
    doc_a = await _seed_document(tenant_a, user_a)

    tenant_context.clear()
    async with app_session.begin():
        assert await _document_count(app_session, doc_a) == 0


async def test_cross_tenant_insert_fails(app_session):
    tenant_a = await _seed_tenant(name="rls-ins-a")
    tenant_b = await _seed_tenant(name="rls-ins-b")

    with tenant_scope(tenant_a):
        async with app_session.begin() as tx:
            failed = False
            try:
                await app_session.execute(
                    text(
                        "INSERT INTO documents (id, tenant_id, owner_id, filename, "
                        "file_path, status, include_in_extraction, is_edited) "
                        "VALUES (:id, :tenant_id, :owner_id, 'x', '/tmp/x', "
                        "'uploaded', false, false)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "tenant_id": str(tenant_b),
                        "owner_id": str(uuid.uuid4()),
                    },
                )
                await tx.commit()
            except Exception:
                await tx.rollback()
                failed = True
    assert failed, "a tenant context inserted a row into another tenant"


async def test_cross_tenant_update_and_delete_are_noops(app_session):
    tenant_a = await _seed_tenant(name="rls-upd-a")
    tenant_b = await _seed_tenant(name="rls-upd-b")
    doc_b = await _seed_document(tenant_b, await _seed_user(tenant_b))

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("UPDATE documents SET filename = 'hijacked' WHERE id = :id"),
                {"id": str(doc_b)},
            )
            assert result.rowcount == 0
            result = await app_session.execute(
                text("DELETE FROM documents WHERE id = :id"), {"id": str(doc_b)}
            )
            assert result.rowcount == 0


async def test_update_cannot_repoint_another_tenant(app_session):
    tenant_a = await _seed_tenant(name="rls-repoint-a")
    tenant_b = await _seed_tenant(name="rls-repoint-b")
    user_a = await _seed_user(tenant_a)
    doc_a = await _seed_document(tenant_a, user_a)

    with tenant_scope(tenant_a):
        async with app_session.begin() as tx:
            failed = False
            try:
                await app_session.execute(
                    text("UPDATE documents SET tenant_id = :b WHERE id = :id"),
                    {"b": str(tenant_b), "id": str(doc_a)},
                )
                await tx.commit()
            except Exception:
                await tx.rollback()
                failed = True
    assert failed, "WITH CHECK did not stop a cross-tenant repoint"


# ---------------------------------------------------------------------------
# Nullable-tenant flavors (system rows visible / hidden)
# ---------------------------------------------------------------------------


async def _seed_global_concept() -> uuid.UUID:
    """A NULL-tenant (system) catalog row, seeded through the ORM defaults."""
    from app.models import Concept

    concept_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Concept(
                id=concept_id,
                slug=f"rls-{concept_id}",
                code=f"RLS-{concept_id}",
                name="probe",
                primary_kind="body_system",
                coding_system="http://probe",
                scope="system",
                tenant_id=None,
            )
        )
        await session.commit()
    return concept_id


async def test_system_catalog_rows_visible_to_tenants(app_session):
    tenant_a = await _seed_tenant(name="rls-cat")
    concept_id = await _seed_global_concept()

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM concepts WHERE id = :id"),
                {"id": str(concept_id)},
            )
            assert int(result.scalar_one()) == 1


async def test_tenant_cannot_mint_or_delete_system_rows(app_session):
    tenant_a = await _seed_tenant(name="rls-sysrow")
    concept_id = await _seed_global_concept()

    with tenant_scope(tenant_a):
        async with app_session.begin() as tx:
            failed = False
            try:
                from app.models import Concept

                app_session.add(
                    Concept(
                        id=uuid.uuid4(),
                        slug=f"rls-mint-{uuid.uuid4()}",
                        code=f"RLS-MINT-{uuid.uuid4()}",
                        name="mint",
                        primary_kind="body_system",
                        coding_system="http://probe",
                        scope="system",
                        tenant_id=None,
                    )
                )
                await tx.commit()
            except Exception:
                await tx.rollback()
                failed = True
    assert failed, "a tenant context minted a NULL-tenant (system) catalog row"

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("DELETE FROM concepts WHERE id = :id"), {"id": str(concept_id)}
            )
            assert result.rowcount == 0, "a tenant deleted a system catalog row"


async def _chain_head_hash(chain_key: str) -> str:
    """The chain's current head hash (genesis when the chain is empty)."""
    from app.services.audit.audit_chain import GENESIS_HASH

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT head_hash FROM audit_chain_heads WHERE chain_key = :chain_key"),
            {"chain_key": chain_key},
        )
        row = result.scalar_one_or_none()
        return row if row else GENESIS_HASH


async def _next_audit_seq(chain_key: str, *, probe_slot: int = 0) -> int:
    """The sequence for a probe row — never a low number on the system chain.

    Other audit fixtures own the low system-chain sequences within a fresh
    test session (the viewer suite seeds ``seq=1``); probes live far above
    any realistic append volume. Tenant chains are probe-private, so
    ``max + 1`` is already safe there.
    """
    if chain_key == "system":
        return 9_000_000 + probe_slot
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events WHERE chain_key = :chain_key"),
            {"chain_key": chain_key},
        )
        return int(result.scalar_one())


async def _seed_audit_row(tenant_id: uuid.UUID | None, action: str) -> None:
    """One honest chain append (real hashes + head advance) as a probe row.

    The probe must never break the chain verifier that other audit tests
    run, so the row is appended through the real append path — not a raw
    insert with fake hashes.
    """
    from app.models.audit_model import AuditEvent
    from app.services.audit.audit_chain import append as chain_append

    async with AsyncSessionLocal() as session:
        # Probe rows are keyed by action — clear any residue from a prior
        # run first so the counts below stay exact (the test DB truncates
        # only at session start).
        await session.execute(
            text("DELETE FROM audit_events WHERE action = :action"), {"action": action}
        )
        await session.commit()
    async with AsyncSessionLocal() as session, session.begin():
        await chain_append(
            session,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=None,
                action=action,
                resource_type="probe",
                outcome="ok",
                created_at=datetime.now(UTC),
                chain_key=f"t:{tenant_id}" if tenant_id else "system",
            ),
        )


async def test_system_audit_chain_hidden_from_tenants(app_session):
    tenant_a = await _seed_tenant(name="rls-audit")
    await _seed_audit_row(None, "rls.system_event")
    await _seed_audit_row(tenant_a, "rls.tenant_event")

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action IN ('rls.system_event', 'rls.tenant_event') "
                    "AND chain_key = 'system'"
                )
            )
            assert int(result.scalar_one()) == 0, "a tenant read the system chain"

    with tenant_context.system_scope("test"):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'rls.system_event'")
            )
            assert int(result.scalar_one()) == 1, "the system actor lost its chain"


async def test_access_elevations_policies(app_session):
    """Elevations keep the hidden-read flavor: a tenant sees its own rows,
    NULL-tenant (system) rows stay hidden from plain tenants, the system
    actor reaches everything, and a tenant can never mint a system row."""
    tenant_a = await _seed_tenant(name="rls-elev-a")
    tenant_b = await _seed_tenant(name="rls-elev-b")
    user_a = await _seed_user(tenant_a)
    await _seed_user(tenant_b)

    async def _seed(tenant, user_id):
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "INSERT INTO access_elevations (id, tenant_id, requested_by, scope, "
                    "status, permissions, reason, duration_minutes) "
                    "VALUES (:id, :tenant_id, :requested_by, :scope, 'pending', "
                    "'[\"audit:read\"]'::jsonb, 'rls probe', 30)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant) if tenant else None,
                    "requested_by": str(user_id),
                    "scope": "system" if tenant is None else "tenant",
                },
            )
            await session.commit()

    await _seed(tenant_a, user_a)
    await _seed(None, user_a)
    try:
        with tenant_scope(tenant_a):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM access_elevations WHERE reason = 'rls probe'")
                )
                assert int(result.scalar_one()) == 1, "tenant A lost its own row"
                result = await app_session.execute(
                    text(
                        "SELECT count(*) FROM access_elevations "
                        "WHERE reason = 'rls probe' AND tenant_id IS NULL"
                    )
                )
                assert int(result.scalar_one()) == 0, "a tenant read a system (NULL-tenant) row"

        with tenant_scope(tenant_b):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM access_elevations WHERE reason = 'rls probe'")
                )
                assert int(result.scalar_one()) == 0, "tenant B saw foreign/system rows"

        with tenant_context.system_scope("test"):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM access_elevations WHERE reason = 'rls probe'")
                )
                assert int(result.scalar_one()) == 2, "the system actor lost full reach"

                result = await app_session.execute(
                    text(
                        "UPDATE access_elevations SET status = 'revoked' "
                        "WHERE reason = 'rls probe' AND tenant_id IS NULL"
                    )
                )
                assert result.rowcount == 1, "the system actor could not contain a window"

        with tenant_scope(tenant_a):
            async with app_session.begin():
                result = await app_session.execute(
                    text(
                        "SELECT count(*) FROM access_elevations "
                        "WHERE reason = 'rls probe' AND tenant_id IS NULL AND status = 'revoked'"
                    )
                )
                assert int(result.scalar_one()) == 0, "the revoked system row leaked back"

        with tenant_scope(tenant_a):
            async with app_session.begin() as tx:
                minted = False
                try:
                    await tx.session.execute(
                        text(
                            "INSERT INTO access_elevations (id, tenant_id, requested_by, scope, "
                            "status, permissions, reason, duration_minutes) "
                            "VALUES (:id, NULL, :requested_by, 'system', 'pending', "
                            "'[]'::jsonb, 'mint', 30)"
                        ),
                        {"id": str(uuid.uuid4()), "requested_by": str(user_a)},
                    )
                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    minted = True
            assert minted, "a tenant context minted a NULL-tenant (system) elevation"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM access_elevations WHERE reason IN ('rls probe', 'mint')")
            )
            await session.commit()


async def test_audit_write_policies(app_session):
    """Tenant rows need the tenant match; NULL rows need the system flag."""
    tenant_a = await _seed_tenant(name="rls-audit-write")
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM audit_events WHERE action = 'rls.write_probe'"))
        await session.commit()

    insert_sql = text(
        "INSERT INTO audit_events (id, tenant_id, user_id, action, resource_type, "
        "outcome, chain_key, seq, prev_hash, entry_hash, created_at) "
        "VALUES (:id, :tenant_id, NULL, 'rls.write_probe', 'probe', 'ok', :chain_key, "
        ":seq, :prev, :entry, :created_at)"
    )
    created_at = datetime.now(UTC)

    async def _chain_params(chain_key: str, probe_slot: int) -> dict:
        """Real chain fields — the probe row must survive the chain verifier."""
        from app.services.audit.audit_chain import compute_entry_hash

        seq = await _next_audit_seq(chain_key, probe_slot=probe_slot)
        prev = await _chain_head_hash(chain_key)
        entry = compute_entry_hash(
            chain_key=chain_key,
            seq=seq,
            prev_hash=prev,
            action="rls.write_probe",
            resource_type="probe",
            resource_id=None,
            outcome="ok",
            created_at=created_at,
        )
        return {
            "id": str(uuid.uuid4()),
            "tenant_id": None,
            "chain_key": chain_key,
            "seq": seq,
            "prev": prev,
            "entry": entry,
            "created_at": created_at,
        }

    with tenant_scope(tenant_a):
        async with app_session.begin() as tx:
            failed = False
            try:
                await app_session.execute(insert_sql, await _chain_params("system", 1))
                await tx.commit()
            except Exception:
                await tx.rollback()
                failed = True
    assert failed, "a tenant context wrote into the system audit chain"

    with tenant_scope(tenant_a):
        async with app_session.begin() as tx:
            params = {**await _chain_params(f"t:{tenant_a}", 0), "tenant_id": str(tenant_a)}
            await app_session.execute(insert_sql, params)
            # The raw probe must leave the chain verifiable: advance the
            # head row exactly as the real append path would (under the
            # tenant's own context, like every production append).
            await app_session.execute(
                text(
                    "INSERT INTO audit_chain_heads (chain_key, tenant_id, seq, head_hash) "
                    "VALUES (:chain_key, :tenant_id, :seq, :entry) "
                    "ON CONFLICT (chain_key) DO UPDATE "
                    "SET seq = :seq, head_hash = :entry, tenant_id = :tenant_id"
                ),
                {
                    "chain_key": params["chain_key"],
                    "tenant_id": str(tenant_a),
                    "seq": params["seq"],
                    "entry": params["entry"],
                },
            )
            await tx.commit()


# ---------------------------------------------------------------------------
# Hidden-read tables: the system actor has full reach (ADR 0054 phase 11
# revision — the SYSTEM_ADMIN operator surfaces are cross-tenant)
# ---------------------------------------------------------------------------


async def _task_type_id() -> str:
    """An existing ``ai_task_defs`` row for ai_call_logs probes (global)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id FROM ai_task_defs ORDER BY code LIMIT 1"))
        row = result.scalar_one_or_none()
        if row is not None:
            return str(row)
        task_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO ai_task_defs (id, code, label_key, enabled) "
                "VALUES (:id, 'rls_probe', 'rls probe', true)"
            ),
            {"id": str(task_id)},
        )
        await session.commit()
        return str(task_id)


async def _seed_operator_probe_rows(tenant_id: uuid.UUID, action: str) -> None:
    """One ``ai_call_logs`` row in ``tenant_id`` (the AI-usage surface)."""
    task_type_id = await _task_type_id()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM ai_call_logs WHERE error_class = :probe"),
            {"probe": action},
        )
        await session.execute(
            text(
                "INSERT INTO ai_call_logs (id, tenant_id, task_type_id, route, "
                "started_at, latency_ms, cost_micros, status, error_class) "
                "VALUES (:id, :tenant_id, :task_type_id, 'chat', now(), 1, 0, 'ok', :probe)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "task_type_id": task_type_id,
                "probe": action,
            },
        )
        await session.commit()


async def _seed_erasure_request(tenant_id: uuid.UUID) -> uuid.UUID:
    request_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO erasure_requests (id, tenant_id, subject_type, subject_id, "
                "status, dry_run, report, report_schema_version) "
                "VALUES (:id, :tenant_id, 'user', :subject_id, 'done', true, '{}', 1)"
            ),
            {
                "id": str(request_id),
                "tenant_id": str(tenant_id),
                "subject_id": str(uuid.uuid4()),
            },
        )
        await session.commit()
    return request_id


async def test_system_actor_reads_every_hidden_read_tenant(app_session):
    """``app.system=on`` reaches tenant rows on audit-flavor tables too.

    The pre-revision policy hid foreign-tenant rows from the system actor
    (``system AND tenant_id IS NULL``) — the SYSTEM_ADMIN audit viewer's
    ``?all=true`` stream and ``?tenant_id=<foreign>`` tabs answered empty
    whenever the DB enforced RLS. Plain tenants still see only their own
    rows; NULL-tenant rows stay hidden from them.
    """
    tenant_a = await _seed_tenant(name="rls-hidden-a")
    tenant_b = await _seed_tenant(name="rls-hidden-b")
    await _seed_audit_row(tenant_a, "rls.hidden.a")
    await _seed_audit_row(tenant_b, "rls.hidden.b")
    await _seed_audit_row(None, "rls.hidden.system")

    marker = text(
        "SELECT count(*) FROM audit_events WHERE action IN "
        "('rls.hidden.a', 'rls.hidden.b', 'rls.hidden.system')"
    )

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(marker)
            assert int(result.scalar_one()) == 1, "a plain tenant saw foreign/system rows"
    with tenant_scope(tenant_b):
        async with app_session.begin():
            result = await app_session.execute(marker)
            assert int(result.scalar_one()) == 1

    with tenant_context.system_scope("admin:audit-view"):
        async with app_session.begin():
            result = await app_session.execute(marker)
            assert int(result.scalar_one()) == 3, "the system actor lost cross-tenant reach"
            # ``?tenant_id=<foreign>`` narrowing — the foreign tab is non-empty.
            result = await app_session.execute(
                text("SELECT count(*) FROM audit_events WHERE chain_key = :k"),
                {"k": f"t:{tenant_b}"},
            )
            assert int(result.scalar_one()) == 1

    tenant_context.clear()
    async with app_session.begin():
        result = await app_session.execute(marker)
        assert int(result.scalar_one()) == 0, "the system reach leaked past its scope"


async def test_chain_verifier_walks_a_foreign_tenant_chain(app_session):
    """``?tenant_id=<foreign>`` verifies entries, never an empty ``ok``."""
    from app.services.audit.audit_chain import REASON_EMPTY, verify_audit_chain

    tenant_b = await _seed_tenant(name="rls-verify-b")
    await _seed_audit_row(tenant_b, "rls.verify.foreign")

    with tenant_context.system_scope("admin:verify"):
        async with app_session.begin():
            status = await verify_audit_chain(app_session, f"t:{tenant_b}")
    assert status["entries"] > 0, "the verifier walked a foreign chain blind"
    assert status["reason"] != REASON_EMPTY
    assert status["ok"] is True

    # The same walk under the tenant's own context verifies its chain too.
    with tenant_scope(tenant_b):
        async with app_session.begin():
            status = await verify_audit_chain(app_session, f"t:{tenant_b}")
    assert status["ok"] is True and status["entries"] > 0


async def test_cross_tenant_erasure_ledger_and_ai_usage(app_session):
    """The lifecycle ledger and AI-usage stats reach both tenants as system."""
    tenant_a = await _seed_tenant(name="rls-ops-a")
    tenant_b = await _seed_tenant(name="rls-ops-b")
    await _seed_erasure_request(tenant_a)
    await _seed_erasure_request(tenant_b)
    await _seed_operator_probe_rows(tenant_a, "rls.ai.a")
    await _seed_operator_probe_rows(tenant_b, "rls.ai.b")

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM erasure_requests WHERE report = '{}'::jsonb")
            )
            assert int(result.scalar_one()) == 1, "a tenant saw another tenant's ledger"
            result = await app_session.execute(
                text("SELECT count(*) FROM ai_call_logs WHERE error_class LIKE 'rls.ai.%'")
            )
            assert int(result.scalar_one()) == 1, "a tenant saw another tenant's AI usage"

    with tenant_context.system_scope("admin:lifecycle"):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM erasure_requests WHERE report = '{}'::jsonb")
            )
            assert int(result.scalar_one()) >= 2, "the cross-tenant erasure list is blind"
            result = await app_session.execute(
                text("SELECT count(*) FROM ai_call_logs WHERE error_class LIKE 'rls.ai.%'")
            )
            assert int(result.scalar_one()) == 2, "the cross-tenant AI usage stats are blind"


async def test_switched_system_admin_keeps_full_reach(app_session):
    """A switched SYSTEM_ADMIN stays the system actor; the GUC stays home.

    The switch scopes *authorization* (route policies, tenant-admin
    checks) — the RLS system flag follows the role, and the planted
    tenant GUC carries the **home** tenant (``original_tenant_id``),
    identity-honest rather than the switch target. Both visibility
    consequences are pinned: the switch target's rows and the system
    chain are readable through the switched context.
    """
    from app.core.tenant_context import set_principal

    tenant_home = await _seed_tenant(name="rls-switch-home")
    tenant_target = await _seed_tenant(name="rls-switch-target")
    await _seed_audit_row(tenant_target, "rls.switched.target")
    await _seed_audit_row(None, "rls.switched.system")
    await _seed_audit_row(tenant_home, "rls.switched.home")

    class _SwitchedPrincipal:
        tenant_id = tenant_target
        original_tenant_id = tenant_home
        switched = True
        role = "SYSTEM_ADMIN"
        user_id = uuid.uuid4()
        bound_patient_id = None

    set_principal(_SwitchedPrincipal())
    assert tenant_context.system_enabled() is True, "the switch dropped the system flag"
    assert tenant_context.current_tenant_id() == tenant_home, (
        "a switched principal must plant its home tenant, not the target"
    )

    async with app_session.begin():
        result = await app_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'rls.switched.target'")
        )
        assert int(result.scalar_one()) == 1, "the switch target's rows are blind"
        result = await app_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'rls.switched.system'")
        )
        assert int(result.scalar_one()) == 1, "the system chain is blind while switched"
        result = await app_session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'rls.switched.home'")
        )
        assert int(result.scalar_one()) == 1, "the home tenant's rows are blind"
    tenant_context.clear()


async def test_audit_lane_required_write_as_the_app_role(app_engine, monkeypatch):
    """The dedicated audit lane works end-to-end as the runtime role.

    Production runs the lane on an ``HA_DATABASE_URL`` that connects as
    ``neuronection_health_app``; this test binds the same
    ``log_audit_action`` path (chain-head lock + append + head advance) to
    an app-role pool with no ambient context — the lane must plant the
    event's own chain context and the required write must succeed.
    """
    import app.services.audit.audit_service as audit_service

    tenant = await _seed_tenant(name="rls-lane-app-role")
    monkeypatch.setattr(audit_service, "AuditSessionLocal", app_engine)
    monkeypatch.setattr(audit_service, "AUDIT_DATABASE_AVAILABLE", True)

    tenant_context.clear()
    await audit_service.log_audit_action(
        tenant_id=tenant,
        user_id=None,
        action="rls.lane_app_role",
        resource_type="probe",
        required=True,
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'rls.lane_app_role'")
        )
        assert int(result.scalar_one()) == 1


# ---------------------------------------------------------------------------
# Tenant-derived children
# ---------------------------------------------------------------------------


async def test_child_table_rides_the_parent_policy(app_session):
    tenant_a = await _seed_tenant(name="rls-chat-a")
    tenant_b = await _seed_tenant(name="rls-chat-b")
    _session_a, message_a = await _chat_thread(tenant_a, "probe-a")

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM chat_messages WHERE id = :id"),
                {"id": str(message_a)},
            )
            assert int(result.scalar_one()) == 1

    with tenant_scope(tenant_b):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM chat_messages WHERE id = :id"),
                {"id": str(message_a)},
            )
            assert int(result.scalar_one()) == 0


async def test_seed_pack_chain_anchors_on_the_system_source(app_session):
    """seed_deferred_refs → versions → sources (NULL-tenant system source)."""
    source_id = uuid.uuid4()
    version_id = uuid.uuid4()
    ref_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO seed_sources (id, kind, uri, ref_spec, scope, "
                "tenant_id, priority) VALUES (:id, 'bundled', 'bundled://probe', "
                "'pack.json', 'system', NULL, 100)"
            ),
            {"id": str(source_id)},
        )
        await session.execute(
            text(
                "INSERT INTO seed_source_versions (id, source_id, digest, status, "
                "contract_version) VALUES (:id, :source_id, 'deadbeef', 'approved', "
                "'1')"
            ),
            {"id": str(version_id), "source_id": str(source_id)},
        )
        await session.execute(
            text(
                "INSERT INTO seed_deferred_refs (id, source_version_id, payload, "
                "status) VALUES (:id, :version_id, '{}', 'pending')"
            ),
            {"id": str(ref_id), "version_id": str(version_id)},
        )
        await session.commit()

    tenant_a = await _seed_tenant(name="rls-seed")
    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM seed_deferred_refs WHERE id = :id"),
                {"id": str(ref_id)},
            )
            assert int(result.scalar_one()) == 0, (
                "a tenant read a system seed source's deferred refs"
            )

    with tenant_context.system_scope("test"):
        async with app_session.begin():
            result = await app_session.execute(
                text("SELECT count(*) FROM seed_deferred_refs WHERE id = :id"),
                {"id": str(ref_id)},
            )
            assert int(result.scalar_one()) == 1


# ---------------------------------------------------------------------------
# Pool hygiene
# ---------------------------------------------------------------------------


async def test_pool_hygiene_no_context_bleed(app_engine):
    tenant_a = await _seed_tenant(name="rls-pool-a")
    tenant_b = await _seed_tenant(name="rls-pool-b")
    user_a = await _seed_user(tenant_a)
    user_b = await _seed_user(tenant_b)
    doc_a = await _seed_document(tenant_a, user_a)
    doc_b = await _seed_document(tenant_b, user_b)

    session_a = app_engine()
    session_b = app_engine()
    try:
        with tenant_scope(tenant_a):
            async with session_a.begin():
                assert await _document_count(session_a, doc_a) == 1
        with tenant_scope(tenant_b):
            async with session_b.begin():
                assert await _document_count(session_b, doc_b) == 1
                assert await _document_count(session_b, doc_a) == 0
        with tenant_scope(tenant_a):
            async with session_a.begin():
                assert await _document_count(session_a, doc_b) == 0
                assert await _document_count(session_a, doc_a) == 1
        # An unset context after both tenant transactions: the hook's
        # re-plant writes empty GUCs, so the pooled connections answer
        # nothing — a stale SET LOCAL can never leak across checkouts.
        tenant_context.clear()
        async with session_a.begin():
            assert await _document_count(session_a, doc_a) == 0
        async with session_b.begin():
            assert await _document_count(session_b, doc_b) == 0
    finally:
        await session_a.close()
        await session_b.close()


# ---------------------------------------------------------------------------
# Timescale: the documented exception + the scoped views
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def has_timescaledb(owner_sync_engine):
    with owner_sync_engine.connect() as conn:
        return bool(
            conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")).scalar()
        )


async def test_telemetry_data_is_the_documented_exception(owner_sync_engine):
    with owner_sync_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='telemetry_data'"
            )
        ).scalar()
    assert row is False, "telemetry_data gained RLS — update the exception inventory"


async def test_scoped_cagg_views_filter_by_context(app_session, has_timescaledb, owner_sync_engine):
    if not has_timescaledb:
        pytest.skip("TimescaleDB not available")
    tenant_a = await _seed_tenant(name="rls-cagg-a")
    tenant_b = await _seed_tenant(name="rls-cagg-b")
    now = datetime.now(UTC)
    since = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    async with AsyncSessionLocal() as session:
        for tenant, slug in ((tenant_a, "hr-a"), (tenant_b, "hr-b")):
            await session.execute(
                text(
                    "INSERT INTO telemetry_data (id, timestamp, tenant_id, device_id, "
                    "patient_id, slug, value, raw_value, raw_unit, unit) "
                    "VALUES (:id, :ts, :tenant_id, :device_id, :patient_id, :slug, "
                    "1.0, 1.0, 'bpm', 'bpm')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "ts": now,
                    "tenant_id": str(tenant),
                    "device_id": str(uuid.uuid4()),
                    "patient_id": str(uuid.uuid4()),
                    "slug": slug,
                },
            )
        await session.commit()

    # The realtime union only serves buckets past the refresh watermark —
    # materialize explicitly so the test exercises the *materialized* path
    # (the one RLS can never cover and the scoped view exists for).
    with owner_sync_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("CALL refresh_continuous_aggregate('telemetry_hourly', NULL, NULL)"))

    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(
                text(
                    "SELECT DISTINCT tenant_id FROM telemetry_hourly_scoped WHERE bucket >= :since"
                ),
                {"since": since},
            )
            tenants = {str(row[0]) for row in result.all()}
    assert tenants == {str(tenant_a)}, f"the hourly view leaked tenants: {tenants}"

    tenant_context.clear()
    async with app_session.begin():
        result = await app_session.execute(
            text("SELECT count(*) FROM telemetry_hourly_scoped WHERE bucket >= :since"),
            {"since": since},
        )
        assert int(result.scalar_one()) == 0, "the scoped view answered without a GUC"


# ---------------------------------------------------------------------------
# Context wiring: presign, facade, auth, audit, workers
# ---------------------------------------------------------------------------


async def test_presign_context_selects_only_the_token_tenant(app_session):
    from app.core.security import create_presigned_token, verify_presigned_token
    from app.core.tenant_context import set_tenant as set_tenant_context

    tenant_a = await _seed_tenant(name="rls-presign-a")
    tenant_b = await _seed_tenant(name="rls-presign-b")
    doc_a = await _seed_document(tenant_a, await _seed_user(tenant_a))
    doc_b = await _seed_document(tenant_b, await _seed_user(tenant_b))

    token = create_presigned_token(str(doc_a), user_id=str(uuid.uuid4()), tenant_id=str(tenant_a))
    claims = verify_presigned_token(token, str(doc_a))
    assert claims is not None

    # The download endpoint's exact sequence: verify → plant the token's
    # tenant → query.
    set_tenant_context(claims["tenant_id"], actor=str(claims["user_id"]))
    async with app_session.begin():
        assert await _document_count(app_session, doc_a) == 1
        assert await _document_count(app_session, doc_b) == 0

    # A foreign tenant's document is invisible — the same 404 the endpoint
    # answers, without an existence leak.
    foreign = create_presigned_token(str(doc_b), user_id=str(uuid.uuid4()), tenant_id=str(tenant_a))
    foreign_claims = verify_presigned_token(foreign, str(doc_b))
    assert foreign_claims is not None
    set_tenant_context(foreign_claims["tenant_id"], actor="")
    async with app_session.begin():
        assert await _document_count(app_session, doc_b) == 0


async def test_facade_principal_plants_tenant_and_patient_context():
    from app.core.security import create_api_access_token, get_api_principal

    tenant = await _seed_tenant(name="rls-facade")
    patient_id = uuid.uuid4()
    token, _ = create_api_access_token(
        client_id="rls-client",
        tenant_id=str(tenant),
        scopes=["patient/Observation.read"],
        bound_patient_id=str(patient_id),
    )
    principal = await get_api_principal(token=token)
    assert principal.tenant_id == tenant
    assert tenant_context.current_tenant_id() == tenant
    assert tenant_context.system_enabled() is False
    assert tenant_context.current_patient_id() == patient_id
    tenant_context.clear()


async def test_session_principal_plants_context():
    """The session verifier plants the tenant context (never the system flag)."""
    from app.core import tenant_context as ctx
    from app.core.security import authenticate_session_token
    from tests._auth_helpers import create_user, sign_in

    tenant = await _seed_tenant(name="rls-session-ctx")
    user = await create_user(role="USER", tenant_id=tenant)
    issued = await sign_in(user)
    principal = await authenticate_session_token(issued.access_token)
    assert principal.tenant_id == tenant
    assert ctx.current_tenant_id() == tenant
    assert ctx.system_enabled() is False
    tenant_context.clear()


async def test_system_admin_principal_plants_the_system_flag():
    from app.core import tenant_context as ctx
    from tests._auth_helpers import create_user

    user = await create_user(role="SYSTEM_ADMIN")
    from app.core.tenant_context import set_principal

    class _P:
        tenant_id = user.tenant_id
        role = "SYSTEM_ADMIN"
        user_id = user.id
        switched = False
        bound_patient_id = None

    set_principal(_P())
    assert ctx.system_enabled() is True
    assert ctx.current_tenant_id() == user.tenant_id
    tenant_context.clear()


async def test_audit_lane_writes_the_system_chain_required():
    """The dedicated lane plants the event's chain context (fail-closed)."""
    from app.services.audit.audit_service import log_audit_action

    tenant = await _seed_tenant(name="rls-audit-lane")
    # A tenant-chain write with no ambient context — the lane plants the
    # event's tenant itself and the required write succeeds as the app
    # role... in production. In the test env the audit pool rides the
    # owner URL, so the pin here is the context mechanics: the write
    # succeeds and the row carries the right chain.
    await log_audit_action(
        tenant_id=tenant,
        user_id=None,
        action="rls.lane_probe",
        resource_type="probe",
        required=True,
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'rls.lane_probe'")
        )
        assert int(result.scalar_one()) == 1


def test_worker_context_is_explicit_and_restored():
    """``async_task`` plants the system scope at the task boundary only."""

    from app.workers.tasks import async_task

    seen: list[bool] = []

    @async_task
    async def _probe():
        seen.append(tenant_context.system_enabled())
        with tenant_scope(uuid.uuid4()):
            seen.append(tenant_context.system_enabled())

    _probe()
    assert seen == [True, False], "the worker system scope leaked"
    assert tenant_context.system_enabled() is False


# ---------------------------------------------------------------------------
# Perf: the policy expression evaluates once (InitPlan), not per row
# ---------------------------------------------------------------------------


async def test_policy_expression_is_an_initplan(app_session):
    tenant_a = await _seed_tenant(name="rls-perf")
    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(text("EXPLAIN SELECT count(*) FROM documents"))
            plan = "\n".join(row[0] for row in result.all())
    assert "InitPlan" in plan, "the GUC reads are not hoisted into InitPlans"
    assert "SubPlan" not in plan, "the GUC reads evaluate per row"


async def test_child_chain_policy_guc_reads_are_hoisted(app_session):
    """A tenant-derived child's policy stays index-friendly too.

    The parent ``EXISTS`` is a **hashed** SubPlan (materialized once, probed
    per row) and the GUC reads inside it hoist into InitPlans of that
    subplan — never a correlated per-row re-evaluation of
    ``current_setting`` (chat_messages → chat_sessions, one FK hop).
    """
    tenant_a = await _seed_tenant(name="rls-perf-child")
    _session_id, _message_id = await _chat_thread(tenant_a, "rls-perf-child")
    with tenant_scope(tenant_a):
        async with app_session.begin():
            result = await app_session.execute(text("EXPLAIN SELECT count(*) FROM chat_messages"))
            plan = "\n".join(row[0] for row in result.all())
    assert "InitPlan" in plan, "the child chain's GUC reads are not hoisted"
    assert "hashed SubPlan" in plan, "the parent EXISTS is not materialized once"


async def test_enterprise_identity_policies(app_session):
    """The Phase-12B tables keep the tenant wall: tenant rows answer only
    their own tenant context, the system actor reaches everything, and the
    parent-anchored membership table rides ``scim_groups``' policy."""
    tenant_a = await _seed_tenant(name="rls-eid-a")
    tenant_b = await _seed_tenant(name="rls-eid-b")
    user_a = await _seed_user(tenant_a)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO tenant_sso_configs (id, tenant_id, issuer, client_id, "
                "client_secret_ref, default_role) "
                "VALUES (:id, :tenant, 'https://eid.example', 'cid', 'slot', 'USER')"
            ),
            {"id": str(uuid.uuid4()), "tenant": str(tenant_a)},
        )
        await session.execute(
            text(
                "INSERT INTO user_identities (id, tenant_id, user_id, issuer, subject, "
                "email, email_verified) VALUES (:id, :tenant, :user_id, "
                "'https://eid.example', :subject, :email, true)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": str(tenant_a),
                "user_id": str(user_a),
                "subject": "rls-sub-1",
                "email": f"rls-{user_a}@test.local",
            },
        )
        await session.execute(
            text(
                "INSERT INTO scim_tokens (id, tenant_id, name, token_hash) "
                "VALUES (:id, :tenant, 'rls', :hash)"
            ),
            {"id": str(uuid.uuid4()), "tenant": str(tenant_a), "hash": "a" * 64},
        )
        await session.execute(
            text(
                "INSERT INTO scim_groups (id, tenant_id, display_name) "
                "VALUES (:id, :tenant, 'RLS Probe')"
            ),
            {"id": str(uuid.uuid4()), "tenant": str(tenant_a)},
        )
        group_id = (
            await session.execute(
                text("SELECT id FROM scim_groups WHERE display_name = 'RLS Probe'")
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO scim_group_members (id, group_id, user_id) "
                "VALUES (:id, :group_id, :user_id)"
            ),
            {"id": str(uuid.uuid4()), "group_id": str(group_id), "user_id": str(user_a)},
        )
        await session.commit()

    try:
        with tenant_scope(tenant_a):
            async with app_session.begin():
                for table in ("tenant_sso_configs", "user_identities", "scim_tokens"):
                    result = await app_session.execute(text(f"SELECT count(*) FROM {table}"))
                    assert int(result.scalar_one()) >= 1, f"{table} unreadable in its tenant"
                result = await app_session.execute(text("SELECT count(*) FROM scim_group_members"))
                assert int(result.scalar_one()) == 1, "membership row lost via the parent policy"

        with tenant_scope(tenant_b):
            async with app_session.begin():
                for table in ("tenant_sso_configs", "user_identities", "scim_tokens"):
                    result = await app_session.execute(text(f"SELECT count(*) FROM {table}"))
                    assert int(result.scalar_one()) == 0, f"tenant B read {table} foreign rows"
                result = await app_session.execute(text("SELECT count(*) FROM scim_group_members"))
                assert int(result.scalar_one()) == 0, "tenant B read foreign membership"

        with tenant_context.system_scope("test"):
            async with app_session.begin():
                result = await app_session.execute(text("SELECT count(*) FROM scim_group_members"))
                assert int(result.scalar_one()) == 1, "the system actor lost reach"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": str(tenant_a), "b": str(tenant_b)},
            )
            await session.commit()


async def test_sso_domain_claim_reaches_across_tenants_as_the_app_role(app_session):
    """A tenant admin's domain-claim check must see every tenant's claim.

    The admin request runs under its own tenant's RLS context, so a scan that
    stays on that wall only sees the caller's configs — tenant B's admin could
    then enable a config claiming tenant A's domain and take over the
    login-page routing. Both mutation paths (create-enabled and the later
    enable) answer the typed 409.
    """
    from app.core.errors import ConflictError
    from app.services.auth import sso

    tenant_a = await _seed_tenant(name="rls-sso-claim-a")
    tenant_b = await _seed_tenant(name="rls-sso-claim-b")
    admin_a = await _seed_user(
        tenant_a, email=f"sso-admin-{uuid.uuid4().hex[:8]}@test.local", role="ADMIN"
    )

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO tenant_sso_configs (id, tenant_id, issuer, client_id, "
                "client_secret_ref, allowed_domains, default_role, enabled) "
                "VALUES (:id, :tenant, 'https://idp-b.example', 'cid-b', 'slot-b', "
                "'[\"claimed.example\"]'::jsonb, 'USER', true)"
            ),
            {"id": str(uuid.uuid4()), "tenant": str(tenant_b)},
        )
        await session.commit()

    try:
        with tenant_scope(tenant_a):
            # Create-enabled against tenant B's claim → 409.
            async with app_session.begin():
                with pytest.raises(ConflictError) as create_refused:
                    await sso.create_sso_config(
                        app_session,
                        tenant_id=tenant_a,
                        actor_id=admin_a,
                        issuer="https://idp-a.example",
                        client_id="cid-a",
                        client_secret_ref="slot-a",
                        allowed_domains=["claimed.example"],
                        default_role="USER",
                        group_role_map={},
                        enabled=True,
                    )
                assert "claimed.example" in str(create_refused.value)

            # A disabled config claims nothing; enabling it later → 409.
            async with app_session.begin():
                disabled = await sso.create_sso_config(
                    app_session,
                    tenant_id=tenant_a,
                    actor_id=admin_a,
                    issuer="https://idp-a.example",
                    client_id="cid-a",
                    client_secret_ref="slot-a",
                    allowed_domains=["claimed.example"],
                    default_role="USER",
                    group_role_map={},
                    enabled=False,
                )

            async with app_session.begin():
                with pytest.raises(ConflictError) as enable_refused:
                    await sso.update_sso_config(
                        app_session,
                        tenant_id=tenant_a,
                        config_id=disabled.id,
                        actor_id=admin_a,
                        changes={"enabled": True},
                    )
                assert "claimed.example" in str(enable_refused.value)
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": str(tenant_a), "b": str(tenant_b)},
            )
            await session.commit()


async def test_webauthn_credential_policies(app_session):
    """The Phase-12C2 credential table keeps the tenant wall: an account's
    passkeys are visible only inside their tenant, the system actor reaches
    them, and a tenant context cannot insert another tenant's credential."""
    tenant_a = await _seed_tenant(name="rls-passkey-a")
    tenant_b = await _seed_tenant(name="rls-passkey-b")
    user_a = await _seed_user(tenant_a, email=f"passkey-{uuid.uuid4().hex[:8]}@test.local")
    user_b = await _seed_user(tenant_b, email=f"passkey-{uuid.uuid4().hex[:8]}@test.local")

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO webauthn_credentials "
                "(id, tenant_id, user_id, credential_id, public_key, name) "
                "VALUES (:id, :tenant, :user_id, :credential_id, :public_key, 'RLS Probe')"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": str(tenant_a),
                "user_id": str(user_a),
                "credential_id": uuid.uuid4().bytes + uuid.uuid4().bytes,
                "public_key": b"cose-probe",
            },
        )
        await session.commit()

    try:
        with tenant_scope(tenant_a):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM webauthn_credentials")
                )
                assert int(result.scalar_one()) == 1, "tenant A cannot read its credential"

        with tenant_scope(tenant_b):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM webauthn_credentials")
                )
                assert int(result.scalar_one()) == 0, "tenant B read a foreign credential"
                with pytest.raises(DBAPIError):
                    await app_session.execute(
                        text(
                            "INSERT INTO webauthn_credentials "
                            "(id, tenant_id, user_id, credential_id, public_key, name) "
                            "VALUES (:id, :tenant, :user_id, :credential_id, :public_key, 'x')"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "tenant": str(tenant_a),
                            "user_id": str(user_b),
                            "credential_id": uuid.uuid4().bytes + uuid.uuid4().bytes,
                            "public_key": b"cose-probe",
                        },
                    )

        with tenant_context.system_scope("test"):
            async with app_session.begin():
                result = await app_session.execute(
                    text("SELECT count(*) FROM webauthn_credentials")
                )
                assert int(result.scalar_one()) >= 1, "the system actor lost reach"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                {"a": str(tenant_a), "b": str(tenant_b)},
            )
            await session.commit()

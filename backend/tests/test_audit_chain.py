"""The audit hash chain — tamper-evidence, failure policy, redaction (Phase 4A).

Pinned here:

1. **Chain append** — ``log_audit_action`` chains every row per tenant
   (``chain_key = "t:<uuid>"``) and the system chain (``"system"``):
   ``seq`` starts at 1, ``prev_hash`` links to the previous
   ``entry_hash`` (genesis: 64 zeros), and the head row tracks the last
   position. Concurrent appends serialize on the head row — 24 parallel
   appends produce the sequences 1..24 exactly once each and a valid
   chain.
2. **Verifier** — ``verify_audit_chain`` walks seq contiguity, hash links
   and recomputation: an intact chain verifies ``ok``; a tampered row
   (``entry_hash_mismatch``), a deleted middle row (``gap``), a deleted
   head (``missing_head``) and a lagging head (``head_seq_mismatch``)
   each return ``ok=False`` with the closed-vocabulary reason and the
   first bad sequence number. A gap IS a tamper alarm — no resync.
3. **Failure policy (S3)** — ``required=True`` raises
   ``AuditWriteError`` when the audit lane fails; best-effort swallows
   with a CRITICAL alarm. The audit lane runs on its own pool
   (``AUDIT_DB_POOL_SIZE``, test-pinned).
4. **Redaction** — PHI keys (identity, contact, free text) never reach
   the stored diff; id-shaped operational keys pass through.
5. **Sanctioned mutations don't alarm** — erasure-style nulling of the
   actor/diff payload leaves the chain verifiable (the hash covers
   immutable fields only).
6. **Authz gate denials (S7)** — a gate-level 403 records exactly one
   ``authz_denied`` row and fails closed when that record cannot be
   written; ``audit_read`` never runs for a gate denial, so no request
   is double-recorded.
7. **Presign hygiene (S8)** — download tokens carry ``tenant_id`` +
   ``user_id``; download re-verifies the tenant against the document and
   the user against the live row: cross-tenant tokens, tokens of
   deactivated users and legacy claim-less tokens all answer 401.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal, AuditSessionLocal
from app.main import app
from app.models.audit_model import AuditChainHead, AuditEvent
from app.models.enums import Role
from app.models.user_model import UserModel
from app.services.audit import audit_chain
from app.services.audit.audit_chain import (
    GENESIS_HASH,
    SYSTEM_CHAIN_KEY,
    canonical_timestamp,
    chain_key_for,
    compute_entry_hash,
    verify_all_chains,
    verify_audit_chain,
)
from app.services.audit.audit_service import (
    AuditWriteError,
    log_audit_action,
)
from app.services.audit.redaction import REDACTED, redact_diffs

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def clean_audit_tenant() -> uuid.UUID:
    """A fresh tenant: its audit chain starts empty."""
    from tests._auth_helpers import create_tenant

    return await create_tenant(name=f"Audit Chain {uuid.uuid4().hex[:6]}")


async def _purge_chain(chain_key: str) -> None:
    """Delete a synthetic chain outright (rows + head), committed.

    The tamper tests commit their corruption so the verifier — reading on
    the audit lane — can see it; without the purge the shared test DB
    carries permanently broken chains and any later
    ``GET /admin/audit/verify?all=true`` (the viewer suite) answers
    not-ok. A purged chain is indistinguishable from a dead tenant's —
    verify-all covers only chains that still have rows or a head.
    """
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(delete(AuditEvent).where(AuditEvent.chain_key == chain_key))
        await db.execute(delete(AuditChainHead).where(AuditChainHead.chain_key == chain_key))


@pytest_asyncio.fixture
async def tampered_chain(clean_audit_tenant):
    """A fresh tenant chain a test may corrupt in place — purged afterwards
    (see :func:`_purge_chain`)."""
    yield clean_audit_tenant
    await _purge_chain(chain_key_for(clean_audit_tenant))


@pytest_asyncio.fixture
async def api_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def tolerant_client():
    """A client that returns the 500 response instead of re-raising.

    The generic Exception handler answers 500 through ServerErrorMiddleware,
    which re-raises the original exception after handling it — the plain
    ASGITransport surfaces that to the test, this one accepts the response.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def user_headers() -> dict[str, str]:
    from app.models.enums import Role
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user(role=Role.USER, full_name="Audit Chain User")
    return await auth_headers(user)


def _db():
    """A standalone audit-lane session (mirrors the verifier endpoint)."""
    return AuditSessionLocal()


async def _chain_rows(tenant_id: uuid.UUID) -> list[AuditEvent]:
    """Every audit row of one tenant chain, in sequence order."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.chain_key == chain_key_for(tenant_id))
                .order_by(AuditEvent.seq)
            )
        ).scalars()
        return list(rows)


async def _head(tenant_id: uuid.UUID | None) -> AuditChainHead | None:
    async with AsyncSessionLocal() as db:
        return await db.get(AuditChainHead, chain_key_for(tenant_id))


async def _log(**kwargs) -> None:
    """One ``log_audit_action`` call with the boilerplate filled in."""
    params = {
        "tenant_id": None,
        "user_id": uuid.uuid4(),
        "action": "test.chain_event",
        "resource_type": "Test",
    }
    params.update(kwargs)
    await log_audit_action(**params)


# ---------------------------------------------------------------------------
# 1. chain append
# ---------------------------------------------------------------------------


async def test_chain_appends_link_and_advance_head(clean_audit_tenant):
    tenant = clean_audit_tenant
    for i in range(3):
        await _log(tenant_id=tenant, action=f"test.event_{i}")

    rows = await _chain_rows(tenant)
    assert [r.seq for r in rows] == [1, 2, 3]
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[2].prev_hash == rows[1].entry_hash
    for row in rows:
        assert row.entry_hash == compute_entry_hash(
            chain_key=row.chain_key,
            seq=row.seq,
            prev_hash=row.prev_hash,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            outcome=row.outcome,
            created_at=row.created_at,
        )

    head = await _head(tenant)
    assert head is not None
    assert head.seq == 3
    assert head.head_hash == rows[-1].entry_hash
    assert head.tenant_id == tenant


async def test_system_chain_is_separate_from_tenant_chains(clean_audit_tenant):
    tenant = clean_audit_tenant
    system_head_before = await _head(None)
    before_seq = system_head_before.seq if system_head_before is not None else 0

    await _log(tenant_id=tenant)
    await _log(tenant_id=None, action="system.event")

    tenant_head = await _head(tenant)
    system_head = await _head(None)
    assert tenant_head.seq == 1 and tenant_head.tenant_id == tenant
    assert system_head.chain_key == SYSTEM_CHAIN_KEY
    assert system_head.seq == before_seq + 1 and system_head.tenant_id is None

    async with AsyncSessionLocal() as db:
        system_rows = (
            await db.execute(select(AuditEvent).where(AuditEvent.chain_key == SYSTEM_CHAIN_KEY))
        ).scalars()
        system_rows = list(system_rows)
        latest_system = max(system_rows, key=lambda row: row.seq)
        tenant_rows = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.chain_key == chain_key_for(tenant))
            )
        ).scalars()
        assert len(list(tenant_rows)) == 1
    assert len(system_rows) >= 1
    assert latest_system.action == "system.event" and latest_system.seq == system_head.seq


async def test_concurrent_appends_serialize_per_chain(clean_audit_tenant):
    tenant = clean_audit_tenant
    await asyncio.gather(*[_log(tenant_id=tenant) for _ in range(24)])

    rows = await _chain_rows(tenant)
    assert sorted(r.seq for r in rows) == list(range(1, 25))
    head = await _head(tenant)
    assert head.seq == 24
    assert head.head_hash == next(r.entry_hash for r in rows if r.seq == 24)
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is True and status["entries"] == 24


# ---------------------------------------------------------------------------
# 2. verifier
# ---------------------------------------------------------------------------


async def test_verify_reports_ok_and_empty(clean_audit_tenant):
    tenant = clean_audit_tenant
    async with _db() as db:
        empty = await verify_audit_chain(db, chain_key_for(tenant))
    assert empty == {
        "ok": True,
        "chain_key": chain_key_for(tenant),
        "entries": 0,
        "head_seq": None,
        "first_bad_seq": None,
        "reason": "empty",
    }

    await _log(tenant_id=tenant)
    async with _db() as db:
        ok = await verify_audit_chain(db, chain_key_for(tenant))
    assert ok["ok"] is True and ok["reason"] == "ok" and ok["entries"] == 1


async def test_verify_detects_tampered_row(tampered_chain):
    tenant = tampered_chain
    for _ in range(3):
        await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(
            update(AuditEvent)
            .where(
                AuditEvent.chain_key == chain_key_for(tenant),
                AuditEvent.seq == 2,
            )
            .values(action="tampered.action")
        )
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False
    assert status["reason"] == "entry_hash_mismatch"
    assert status["first_bad_seq"] == 2


async def test_verify_detects_deleted_middle_row_gap(tampered_chain):
    tenant = tampered_chain
    for _ in range(3):
        await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        target = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.chain_key == chain_key_for(tenant),
                    AuditEvent.seq == 2,
                )
            )
        ).scalar_one()
        await db.delete(target)
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False
    assert status["reason"] == "gap"
    assert status["first_bad_seq"] == 3


async def test_verify_detects_deleted_head_and_lagging_head(tampered_chain):
    tenant = tampered_chain
    for _ in range(2):
        await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        head = await db.get(AuditChainHead, chain_key_for(tenant))
        await db.delete(head)
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False and status["reason"] == "missing_head"

    async with AsyncSessionLocal() as db, db.begin():
        db.add(
            AuditChainHead(
                chain_key=chain_key_for(tenant),
                tenant_id=tenant,
                seq=1,
                head_hash=GENESIS_HASH,
            )
        )
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False and status["reason"] == "head_seq_mismatch"

    async with AsyncSessionLocal() as db, db.begin():
        head = await db.get(AuditChainHead, chain_key_for(tenant))
        head.seq = 2
        head.head_hash = GENESIS_HASH
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False and status["reason"] == "head_hash_mismatch"


async def test_verify_all_chains_covers_every_chain(clean_audit_tenant):
    tenant = clean_audit_tenant
    await _log(tenant_id=tenant)
    await _log(tenant_id=None, action="system.event")
    async with _db() as db:
        statuses = await verify_all_chains(db)
    by_key = {status["chain_key"]: status for status in statuses}
    assert chain_key_for(tenant) in by_key and SYSTEM_CHAIN_KEY in by_key
    assert by_key[chain_key_for(tenant)]["ok"] is True
    assert by_key[SYSTEM_CHAIN_KEY]["ok"] is True


class _AppendDuringVerify:
    """A session proxy that appends a real audit row when the verifier
    reads the chain head — a deterministic concurrent-append race.

    The verifier reads rows first, then the head; the append fires on the
    head read, i.e. commits *between* the two statements. The head is then
    one row ahead of the rows snapshot: the pre-S3 verifier alarmed
    (``head_seq_mismatch``), the re-read tolerant one verifies ``ok``.
    """

    def __init__(self, inner, tenant_id: uuid.UUID):
        self._inner = inner
        self._tenant_id = tenant_id
        self.appended = False

    async def get(self, model, key, **kwargs):
        if model is AuditChainHead and not self.appended:
            self.appended = True
            await _log(tenant_id=self._tenant_id)
        return await self._inner.get(model, key, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_concurrent_append_during_verification_does_not_alarm(caplog, clean_audit_tenant):
    """S3 — an append committing while the verifier walks the chain is a
    live writer, not tampering: the head-only mismatch is re-read once and
    the race stays silent (no ``AUDIT_CHAIN_ALARM``)."""
    tenant = clean_audit_tenant
    await _log(tenant_id=tenant)
    with caplog.at_level("CRITICAL", logger="app.services.audit.audit_chain"):
        async with _db() as db:
            status = await verify_audit_chain(
                _AppendDuringVerify(db, tenant), chain_key_for(tenant)
            )
    assert status["ok"] is True
    assert status["entries"] == 2  # the seeded row + the concurrent append
    assert audit_chain.CHAIN_TAMPER_ALARM not in caplog.text


async def test_persistent_head_mismatch_still_alarms_after_retry(caplog, tampered_chain):
    """S3 — the re-read is bounded: a genuinely lagging head persists
    across it and alarms (exactly once)."""
    tenant = tampered_chain
    for _ in range(2):
        await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        head = await db.get(AuditChainHead, chain_key_for(tenant))
        head.seq = 1
        head.head_hash = GENESIS_HASH
    with caplog.at_level("CRITICAL", logger="app.services.audit.audit_chain"):
        async with _db() as db:
            status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False and status["reason"] == "head_seq_mismatch"
    assert audit_chain.CHAIN_TAMPER_ALARM in caplog.text
    assert caplog.text.count(audit_chain.CHAIN_TAMPER_ALARM) == 1


async def test_retry_re_reads_the_head_past_the_identity_map(caplog, clean_audit_tenant):
    """Phase 8 follow-up — the retry's head re-read must emit SQL: a head
    cached in the session's identity map from an earlier read would
    otherwise answer ``db.get`` from memory, keep its stale ``seq`` across
    the re-read, and turn a transient lag into a false alarm."""
    tenant = clean_audit_tenant
    await _log(tenant_id=tenant)
    async with _db() as db:
        # Prime the identity map: an earlier read of the head, in this
        # session, before the concurrent append commits.
        stale = await db.get(AuditChainHead, chain_key_for(tenant))
        assert stale.seq == 1
        # A live writer appends + advances the head on its own connection
        # while this session still holds the cached head object.
        await _log(tenant_id=tenant)
        with caplog.at_level("CRITICAL", logger="app.services.audit.audit_chain"):
            status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is True
    assert status["entries"] == 2  # the seeded row + the concurrent append
    assert audit_chain.CHAIN_TAMPER_ALARM not in caplog.text


async def test_deleted_tenant_chain_verifies_empty_and_ok(api_client, system_admin_headers):
    """A deleted (or never-existing) tenant's chain is empty and ok — the
    verifier never crashes on a dead chain and never alarms for one."""
    dead_tenant = uuid.uuid4()
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(dead_tenant))
    assert status == {
        "ok": True,
        "chain_key": chain_key_for(dead_tenant),
        "entries": 0,
        "head_seq": None,
        "first_bad_seq": None,
        "reason": "empty",
    }
    response = await api_client.get(
        f"/api/v1/admin/audit/verify?tenant_id={dead_tenant}",
        headers=system_admin_headers,
    )
    assert response.status_code == 200
    chain = response.json()["chain"]
    assert chain["ok"] is True and chain["entries"] == 0 and chain["reason"] == "empty"


async def test_tamper_alarm_is_logged(caplog, tampered_chain):
    tenant = tampered_chain
    await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(
            update(AuditEvent)
            .where(AuditEvent.chain_key == chain_key_for(tenant))
            .values(action="rewritten")
        )
    import logging as _logging

    _dbg = _logging.getLogger("app.services.audit.audit_chain")
    print("DBG handlers:", _dbg.handlers, "level:", _dbg.level, "propagate:", _dbg.propagate)
    with caplog.at_level("CRITICAL", logger="app.services.audit.audit_chain"):
        async with _db() as db:
            status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is False
    assert audit_chain.CHAIN_TAMPER_ALARM in caplog.text


async def test_sanctioned_payload_rewrites_keep_the_chain_valid(clean_audit_tenant):
    """Erasure/retention mutate only the unhashed payload — no alarm."""
    tenant = clean_audit_tenant
    await _log(tenant_id=tenant)
    await _log(tenant_id=tenant, action="second")
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(
            update(AuditEvent)
            .where(AuditEvent.chain_key == chain_key_for(tenant))
            .values(user_id=None, old_value=None, new_value=None)
        )
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is True


def test_canonical_hash_is_stable_across_timezone_shapes():
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert canonical_timestamp(aware) == "2026-01-01T12:00:00+00:00"
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert canonical_timestamp(naive) == canonical_timestamp(aware)
    base = {
        "chain_key": "t:x",
        "seq": 1,
        "prev_hash": GENESIS_HASH,
        "action": "a",
        "resource_type": "T",
        "resource_id": None,
        "outcome": "ok",
        "created_at": aware,
    }
    with_none = compute_entry_hash(**base)
    with_uuid = compute_entry_hash(**{**base, "resource_id": uuid.uuid4()})
    assert with_none != with_uuid


def test_chain_key_coerces_string_tenant_ids():
    """N2 — a non-normalized string tenant id lands on the same chain as
    its canonical UUID (no silent parallel chain)."""
    tenant = uuid.uuid4()
    upper = str(tenant).upper()
    assert chain_key_for(upper) == chain_key_for(tenant) == f"t:{tenant}"
    assert chain_key_for(None) == SYSTEM_CHAIN_KEY


async def test_log_action_uses_the_coerced_chain_key(clean_audit_tenant):
    """N2 — ``log_audit_action(tenant_id=<str uuid>)`` appends to the
    tenant's canonical chain (and the chain stays verifiable)."""
    tenant = clean_audit_tenant
    await _log(tenant_id=str(tenant).upper())  # non-normalized string form
    rows = await _chain_rows(tenant)
    assert len(rows) == 1
    assert rows[0].chain_key == chain_key_for(tenant)
    async with _db() as db:
        status = await verify_audit_chain(db, chain_key_for(tenant))
    assert status["ok"] is True


# ---------------------------------------------------------------------------
# 3. failure policy (S3)
# ---------------------------------------------------------------------------


class _BrokenLane:
    """A session factory whose sessions explode on use."""

    def __call__(self, *args, **kwargs):
        raise RuntimeError("audit lane down")


async def test_required_write_raises_when_audit_lane_fails(clean_audit_tenant):
    tenant = clean_audit_tenant
    with (
        patch("app.services.audit.audit_service.AuditSessionLocal", _BrokenLane()),
        pytest.raises(AuditWriteError),
    ):
        await _log(tenant_id=tenant, required=True)
    with (
        patch("app.services.audit.audit_service.AUDIT_DATABASE_AVAILABLE", False),
        pytest.raises(AuditWriteError),
    ):
        await _log(tenant_id=tenant, required=True)
    assert await _chain_rows(tenant) == []


async def test_best_effort_write_swallows_lane_failure(caplog, clean_audit_tenant):
    tenant = clean_audit_tenant
    with (
        patch("app.services.audit.audit_service.AuditSessionLocal", _BrokenLane()),
        caplog.at_level("CRITICAL", logger="app.services.audit.audit_service"),
    ):
        await _log(tenant_id=tenant, required=False)
    assert "audit_alarm" in caplog.text
    assert "required AuditEvent failed" not in caplog.text


def test_audit_lane_pool_is_sized_and_pinned():
    """ADR 0054 D3 — the audit lane's pool is its own and test-pinned."""
    assert settings.AUDIT_DB_POOL_SIZE == 4
    audit_engine = AuditSessionLocal.kw["bind"]
    assert audit_engine is not AsyncSessionLocal.kw["bind"]
    assert audit_engine.pool.size() == settings.AUDIT_DB_POOL_SIZE


async def test_phi_read_fails_closed_when_audit_write_fails(tolerant_client):
    """D3 — an unrecordable PHI read answers 500, never the record."""
    from app.models.enums import Role
    from app.models.fhir.patient import Patient
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user(role=Role.USER, full_name="Fail Closed User")
    headers = await auth_headers(user)
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=user.tenant_id,
                user_id=user.id,
                name={"family": "FC", "given": ["Test"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()

    response = await tolerant_client.get(f"/api/v1/patients/{pid}", headers=headers)
    assert response.status_code == 200

    with patch("app.services.audit.audit_service.AuditSessionLocal", _BrokenLane()):
        refused = await tolerant_client.get(f"/api/v1/patients/{pid}", headers=headers)
        assert refused.status_code == 500


# ---------------------------------------------------------------------------
# 4. redaction
# ---------------------------------------------------------------------------


def test_redaction_masks_identity_contact_and_free_text():
    old, new = redact_diffs(
        None,
        {
            "role": "MANAGER",
            "full_name": "Jane Doe",
            "name": {"family": "Doe", "given": ["Jane"]},
            "birth_date": "1990-01-01",
            "birthDate": "1990-01-01",
            "email": "jane@example.com",
            "telecom": [{"system": "phone", "value": "+30 69 1234 5678"}],
            "address": {"city": "Athens", "street": "Main 1"},
            "mrn": "MRN-1",
            "notes": "chest pain history",
            "filename": "mri-jane-doe.dcm",
            "reason": "requested by patient",
        },
    )
    assert new["role"] == "MANAGER"
    assert new["full_name"] == REDACTED
    assert new["name"] == REDACTED
    assert new["birth_date"] == REDACTED and new["birthDate"] == REDACTED
    assert new["email"] == REDACTED
    assert new["telecom"] == REDACTED
    assert new["address"] == REDACTED
    assert new["mrn"] == REDACTED
    assert new["notes"] == REDACTED and new["filename"] == REDACTED
    assert new["reason"] == REDACTED
    assert old is None


def test_redaction_keeps_operational_payload():
    payload = {
        "id": str(uuid.uuid4()),
        "status": "active",
        "permission": "read",
        "http_status": 404,
        "count": 3,
        "nested": {"outcome": "denied", "error": "NotFoundError"},
    }
    _, redacted = redact_diffs(None, payload)
    assert redacted == payload


async def test_stored_audit_row_is_redacted(clean_audit_tenant):
    tenant = clean_audit_tenant
    await _log(
        tenant_id=tenant,
        action="test.redacted_write",
        new_value={"full_name": "Jane Doe", "status": "ok"},
    )
    rows = await _chain_rows(tenant)
    stored = rows[-1].new_value
    assert stored["status"] == "ok"
    assert stored["full_name"] == REDACTED
    assert "Jane Doe" not in stored.values()


# ---------------------------------------------------------------------------
# 5. authz gate denials (S7) — endpoint level
# ---------------------------------------------------------------------------


async def _authz_denied_count() -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authz_denied")
            )
        ).scalar_one()


async def test_gate_denial_records_exactly_one_authz_denied_row(api_client, user_headers):
    """A USER hitting an admin route: one ``authz_denied`` row, one 403."""
    before = await _authz_denied_count()

    response = await api_client.get("/api/v1/admin/audit", headers=user_headers)
    assert response.status_code == 403
    assert response.headers.get("X-Authz-Denied") == "1"

    after = await _authz_denied_count()
    assert after == before + 1
    async with AsyncSessionLocal() as db:
        row = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "authz_denied")
                    .order_by(AuditEvent.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    assert row.outcome == "denied"
    assert row.new_value["permission"] == "audit:read"
    assert row.new_value["cause"] == "role_lacks_permission"


async def test_gate_denial_write_failure_fails_closed(tolerant_client, user_headers):
    """S3 — the gate's denial record is required: lane down ⇒ 500, never 403."""
    with patch(
        "app.core.authz.enforce.log_audit_action",
        side_effect=AuditWriteError("required audit write failed for authz_denied"),
    ):
        response = await tolerant_client.get("/api/v1/admin/audit", headers=user_headers)
        assert response.status_code == 500


async def test_gate_denial_falls_back_to_system_chain_for_dead_tenant():
    """The caller's tenant row is gone (deleted inside the token TTL) — the
    denial's tenant-chain insert hits the FK and the same record falls back
    to the system chain with the tenant id kept in the detail payload."""
    import types

    from app.core.authz import enforce
    from app.schemas.user import TokenData

    dead_tenant = uuid.uuid4()
    request = types.SimpleNamespace()
    request.state = types.SimpleNamespace()
    served = enforce._ServedRoute(path="/api/v1/admin/audit", method="GET", endpoint=None)
    session_user = TokenData(
        sub="ghost@local",
        user_id=uuid.uuid4(),
        tenant_id=dead_tenant,
        role=Role.USER.value,
    )

    await enforce._audit_gate_denial(
        request,
        session_user,
        served,
        reason="role_lacks_permission",
        permission="audit:read",
        role=Role.USER.value,
    )

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "authz_denied")
                    .order_by(AuditEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    fallback = [r for r in rows if (r.new_value or {}).get("chain_fallback") == "system"]
    assert fallback, "the dead-tenant denial must land on the system chain"
    row = fallback[0]
    assert row.chain_key == SYSTEM_CHAIN_KEY
    assert row.tenant_id is None
    assert row.new_value["tenant_id"] == str(dead_tenant)
    assert row.new_value["permission"] == "audit:read"
    assert getattr(request.state, "authz_denial_audited", False) is True


async def test_verifier_endpoint_reports_chain_status(
    api_client, system_admin_headers, clean_audit_tenant
):
    await _log(tenant_id=clean_audit_tenant)
    response = await api_client.get("/api/v1/admin/audit/verify", headers=system_admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["chain"]["chain_key"] == SYSTEM_CHAIN_KEY
    assert body["chain"]["ok"] is True

    tenant_response = await api_client.get(
        f"/api/v1/admin/audit/verify?tenant_id={clean_audit_tenant}",
        headers=system_admin_headers,
    )
    assert tenant_response.status_code == 200
    tenant_body = tenant_response.json()
    assert tenant_body["chain"]["chain_key"] == chain_key_for(clean_audit_tenant)
    assert tenant_body["chain"]["entries"] == 1
    assert tenant_body["chain"]["ok"] is True


async def test_verifier_endpoint_reports_tampering(
    api_client, system_admin_headers, tampered_chain
):
    tenant = tampered_chain
    await _log(tenant_id=tenant)
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(
            update(AuditEvent)
            .where(AuditEvent.chain_key == chain_key_for(tenant))
            .values(action="rewritten")
        )
    response = await api_client.get(
        f"/api/v1/admin/audit/verify?tenant_id={tenant}",
        headers=system_admin_headers,
    )
    assert response.status_code == 200
    chain = response.json()["chain"]
    assert chain["ok"] is False
    assert chain["reason"] == "entry_hash_mismatch"
    assert chain["first_bad_seq"] == 1


async def test_verifier_endpoint_all_and_denies_non_admin(
    api_client, system_admin_headers, user_headers
):
    response = await api_client.get(
        "/api/v1/admin/audit/verify?all=true", headers=system_admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert any(chain["chain_key"] == SYSTEM_CHAIN_KEY for chain in body["chains"])
    assert all(chain["ok"] for chain in body["chains"] if chain["entries"] == 0)

    denied = await api_client.get("/api/v1/admin/audit/verify", headers=user_headers)
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# 6. presign token hygiene (S8)
# ---------------------------------------------------------------------------


def test_presign_token_carries_tenant_and_user_claims():
    from app.core.security import create_presigned_token, verify_presigned_token

    tenant, user, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = create_presigned_token(str(doc), user_id=str(user), tenant_id=str(tenant))
    claims = verify_presigned_token(token, str(doc))
    assert claims is not None
    assert claims["tenant_id"] == str(tenant)
    assert claims["user_id"] == str(user)
    assert verify_presigned_token(token, str(uuid.uuid4())) is None


async def _seed_document(tenant: uuid.UUID, owner: uuid.UUID) -> uuid.UUID:
    """One documents row (no file on disk — the 401 paths never serve it)."""
    from app.models.document_model import DocumentModel

    doc_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            DocumentModel(
                id=doc_id,
                tenant_id=tenant,
                owner_id=owner,
                filename="s8-check.pdf",
                file_path="/tmp/does-not-exist-s8.pdf",
                status="completed",
            )
        )
        await db.commit()
    return doc_id


async def test_download_rejects_cross_tenant_token(tolerant_client, clean_audit_tenant):
    """A token minted by another tenant never serves this tenant's file."""
    from app.core.security import create_presigned_token
    from tests._auth_helpers import create_user

    user = await create_user(role=Role.USER)
    doc_id = await _seed_document(clean_audit_tenant, user.id)
    foreign_tenant = uuid.uuid4()
    token = create_presigned_token(str(doc_id), user_id=str(user.id), tenant_id=str(foreign_tenant))

    response = await tolerant_client.get(f"/api/v1/documents/{doc_id}/download?token={token}")
    assert response.status_code == 401


async def test_download_rejects_token_for_inactive_user(tolerant_client, clean_audit_tenant):
    """Deactivating the minting user kills their outstanding tokens."""
    from sqlalchemy import update

    from app.core.security import create_presigned_token
    from tests._auth_helpers import create_user

    user = await create_user(role=Role.USER, tenant_id=clean_audit_tenant)
    doc_id = await _seed_document(clean_audit_tenant, user.id)
    token = create_presigned_token(
        str(doc_id), user_id=str(user.id), tenant_id=str(clean_audit_tenant)
    )

    async with AsyncSessionLocal() as db:
        await db.execute(update(UserModel).where(UserModel.id == user.id).values(is_active=False))
        await db.commit()

    response = await tolerant_client.get(f"/api/v1/documents/{doc_id}/download?token={token}")
    assert response.status_code == 401


async def test_download_rejects_legacy_token_without_claims(tolerant_client, clean_audit_tenant):
    """Tokens minted before S8 (no tenant/user claims) are invalid."""
    from app.core.security import DOWNLOAD_TOKEN_KIND, _mint
    from tests._auth_helpers import create_user

    user = await create_user(role=Role.USER, tenant_id=clean_audit_tenant)
    doc_id = await _seed_document(clean_audit_tenant, user.id)
    legacy, _ = _mint(DOWNLOAD_TOKEN_KIND, {"doc_id": str(doc_id)}, timedelta(minutes=5))

    response = await tolerant_client.get(f"/api/v1/documents/{doc_id}/download?token={legacy}")
    assert response.status_code == 401

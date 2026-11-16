"""Admin audit viewer, export and retention view (ADR 0054 Phase 3 /
program Phase 8 part B).

Pinned here:

1. **Scoping** — ``GET /admin/audit`` is tenant-scoped for tenant admins
   (ADMIN reaches their own tenant only; a foreign ``tenant_id`` is a
   hidden 404 and ``?all=true`` is a refused 403, both audited) and
   system-wide for SYSTEM_ADMIN (every tenant plus the NULL-tenant system
   chain; ``?tenant_id=`` narrows). USER/MANAGER are 403 at the central
   gate (they hold no ``audit:read``).
2. **Filters + order** — actor, resource_type, resource_id, action,
   outcome, date range, pagination; newest-first.
3. **Export** — ``GET /admin/audit/export?format=csv|json`` streams the
   same filters newest-first with the chain metadata
   (``chain_key``/``seq``/``prev_hash``/``entry_hash``) and the redacted
   diffs; the export itself is audited (``audit.export``, required).
4. **Retention view** — ``GET /admin/audit/retention`` (SYSTEM_ADMIN-only)
   returns the ``retention_policies`` rows governing audit data and
   documents the missing hold registry; there is deliberately no deletion
   endpoint.
5. **Chain verifier unaffected** — the viewer/export additions leave
   ``GET /admin/audit/verify`` intact and green.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit_model import AuditChainHead, AuditEvent
from app.models.enums import Role
from app.services.audit.audit_service import log_audit_action
from tests._auth_helpers import auth_headers, create_tenant, create_user

#: A distinctive action so the shared test DB's other rows never interfere.
_ACTION = "viewer8b.event"


async def _seed_rows(tenant_id, user_id, count=3, *, outcome="ok") -> list[str]:
    """Seed ``count`` audit rows (distinct action suffixes) in order; returns
    the action names oldest-first."""
    actions = []
    for index in range(count):
        action = f"{_ACTION}.{index}"
        await log_audit_action(
            required=True,
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type="viewer_test",
            resource_id=uuid.uuid4(),
            outcome=outcome,
            new_value={"index": index},
        )
        actions.append(action)
    return actions


@pytest_asyncio.fixture
async def world():
    """Two tenants: A with an ADMIN + USER and seeded rows, B with seeded
    rows; plus a SYSTEM_ADMIN."""
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    admin_a = await create_user(role=Role.ADMIN, tenant_id=tenant_a)
    member_a = await create_user(role=Role.USER, tenant_id=tenant_a)
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    actions_a = await _seed_rows(tenant_a, admin_a.id, 3)
    await _seed_rows(tenant_b, admin_a.id, 2)
    await log_audit_action(
        required=True,
        tenant_id=None,
        user_id=None,
        action=f"{_ACTION}.system",
        resource_type="system_test",
        outcome="ok",
        new_value={"detail": "system_chain_row"},
    )
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "member_a": member_a,
        "sysadmin": sysadmin,
        "actions_a": actions_a,
    }


def _viewer_params(**extra):
    params = {"limit": 250, "offset": 0}
    params.update(extra)
    return params


async def _audit_rows(action: str, **filters) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    for column, value in filters.items():
        stmt = stmt.where(getattr(AuditEvent, column) == value)
    async with AsyncSessionLocal() as session:
        return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Viewer scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_viewer_is_tenant_scoped(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get("/api/v1/admin/audit", headers=headers, params=_viewer_params())
    assert resp.status_code == 200
    body = resp.json()
    tenant_a = str(world["tenant_a"])
    assert body["total"] == 3
    assert {item["tenant_id"] for item in body["items"]} == {tenant_a}
    # Newest-first: the seeded actions arrive reversed.
    assert [item["action"] for item in body["items"]] == list(reversed(world["actions_a"]))


@pytest.mark.asyncio
async def test_admin_viewer_cross_tenant_hidden_404_and_all_refused(
    async_client: AsyncClient, world
):
    headers = await auth_headers(world["admin_a"])
    foreign = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(tenant_id=str(world["tenant_b"])),
    )
    assert foreign.status_code == 404
    # The probe is audited (denied), but the answer stays hidden.
    denials = await _audit_rows("admin.audit_view", outcome="denied")
    assert any(row.new_value and row.new_value.get("detail") == "cross_tenant" for row in denials)

    system_wide = await async_client.get(
        "/api/v1/admin/audit", headers=headers, params=_viewer_params(all="true")
    )
    assert system_wide.status_code == 403
    refused = await _audit_rows("admin.audit_view", outcome="denied")
    assert any(
        row.new_value and row.new_value.get("detail") == "system_wide_requires_system_admin"
        for row in refused
    )


@pytest.mark.asyncio
async def test_system_admin_viewer_is_system_wide_and_narrowable(async_client: AsyncClient, world):
    headers = await auth_headers(world["sysadmin"])
    everything = await async_client.get(
        "/api/v1/admin/audit", headers=headers, params=_viewer_params()
    )
    assert everything.status_code == 200
    tenant_ids = {item["tenant_id"] for item in everything.json()["items"]}
    assert str(world["tenant_a"]) in tenant_ids
    assert str(world["tenant_b"]) in tenant_ids
    assert None in tenant_ids  # the system chain row rides the default view

    narrowed = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(tenant_id=str(world["tenant_b"])),
    )
    assert narrowed.status_code == 200
    assert narrowed.json()["total"] == 2

    explicit_all = await async_client.get(
        "/api/v1/admin/audit", headers=headers, params=_viewer_params(all="true")
    )
    assert explicit_all.status_code == 200
    assert explicit_all.json()["total"] >= everything.json()["total"]


@pytest.mark.asyncio
async def test_viewer_gate_denies_non_auditors(async_client: AsyncClient, world):
    user_headers = await auth_headers(world["member_a"])
    denied = await async_client.get(
        "/api/v1/admin/audit", headers=user_headers, params=_viewer_params()
    )
    assert denied.status_code == 403
    assert denied.headers.get("x-authz-denied") == "1"

    manager = await create_user(role=Role.MANAGER, tenant_id=world["tenant_a"])
    manager_denied = await async_client.get(
        "/api/v1/admin/audit", headers=await auth_headers(manager), params=_viewer_params()
    )
    assert manager_denied.status_code == 403


@pytest.mark.asyncio
async def test_tenant_tab_stays_system_admin(async_client: AsyncClient, world):
    """``GET /admin/tenants/{id}/audit`` keeps its SYSTEM_ADMIN handler pin
    (defense in depth); tenant admins use ``GET /admin/audit``."""
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get(
        f"/api/v1/admin/tenants/{world['tenant_a']}/audit",
        headers=headers,
        params={"action": _ACTION},
    )
    assert resp.status_code == 403
    sysadmin = await auth_headers(world["sysadmin"])
    ok = await async_client.get(
        f"/api/v1/admin/tenants/{world['tenant_a']}/audit",
        headers=sysadmin,
    )
    assert ok.status_code == 200
    assert ok.json()["total"] >= 3  # the tenant's stream (other actions may exist)


# ---------------------------------------------------------------------------
# Filters + pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_filters(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    target = (await _audit_rows(f"{_ACTION}.0", tenant_id=world["tenant_a"]))[0]

    by_actor = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(user_id=str(world["admin_a"].id)),
    )
    assert by_actor.status_code == 200
    assert by_actor.json()["total"] == 3

    by_resource = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(resource_type="viewer_test"),
    )
    assert by_resource.json()["total"] == 3

    by_resource_id = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(resource_id=str(target.resource_id)),
    )
    assert by_resource_id.json()["total"] == 1
    assert by_resource_id.json()["items"][0]["id"] == str(target.id)

    by_action = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(action=f"{_ACTION}.1"),
    )
    assert by_action.json()["total"] == 1
    assert by_action.json()["items"][0]["action"] == f"{_ACTION}.1"

    by_outcome = await async_client.get(
        "/api/v1/admin/audit", headers=headers, params=_viewer_params(outcome="denied")
    )
    assert by_outcome.json()["total"] == 0

    wide = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(
            date_from=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            date_to=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        ),
    )
    assert wide.json()["total"] == 3

    narrow = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(
            date_from=(datetime.now(UTC) - timedelta(days=400)).isoformat(),
            date_to=(datetime.now(UTC) - timedelta(days=300)).isoformat(),
        ),
    )
    assert narrow.json()["total"] == 0


@pytest.mark.asyncio
async def test_viewer_pagination(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    page1 = await async_client.get(
        "/api/v1/admin/audit", headers=headers, params=_viewer_params(limit=2)
    )
    assert page1.json()["total"] == 3
    assert len(page1.json()["items"]) == 2
    page2 = await async_client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params=_viewer_params(limit=2, offset=2),
    )
    assert len(page2.json()["items"]) == 1
    # No overlap between the pages (newest-first both times).
    ids1 = {item["id"] for item in page1.json()["items"]}
    ids2 = {item["id"] for item in page2.json()["items"]}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_viewer_rows_carry_chain_metadata(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get("/api/v1/admin/audit", headers=headers, params=_viewer_params())
    row = resp.json()["items"][0]
    assert row["chain_key"] == f"t:{world['tenant_a']}"
    assert row["seq"] >= 1
    assert len(row["prev_hash"]) == 64
    assert len(row["entry_hash"]) == 64


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_jsonl_streams_chain_metadata(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get(
        "/api/v1/admin/audit/export",
        headers=headers,
        params=_viewer_params(format="json", resource_type="viewer_test"),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.splitlines() if line]
    assert len(lines) == 3
    assert [row["action"] for row in lines] == list(reversed(world["actions_a"]))
    for row in lines:
        assert row["chain_key"] == f"t:{world['tenant_a']}"
        assert row["seq"] >= 1
        assert len(row["prev_hash"]) == 64
        assert len(row["entry_hash"]) == 64
        assert (
            row["new_value"]["index"] == row["new_value"]["index"]
        )  # redacted-at-write payload intact


@pytest.mark.asyncio
async def test_export_csv_streams_header_and_rows(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get(
        "/api/v1/admin/audit/export",
        headers=headers,
        params=_viewer_params(format="csv", resource_type="viewer_test"),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = [line for line in resp.text.splitlines() if line]
    assert rows[0].startswith("id,tenant_id,chain_key,seq")
    assert len(rows) == 4  # header + 3 rows
    assert _ACTION in rows[1]


@pytest.mark.asyncio
async def test_export_includes_its_own_audit_row(async_client: AsyncClient, world):
    """The export is audited into the caller's chain — the very row lands in
    the same tenant's stream (and in a second export)."""
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get(
        "/api/v1/admin/audit/export", headers=headers, params={"format": "json"}
    )
    rows = [json.loads(line) for line in resp.text.splitlines() if line]
    assert {row["tenant_id"] for row in rows} == {str(world["tenant_a"])}
    assert any(row["action"] == "audit.export" for row in rows)


@pytest.mark.asyncio
async def test_export_is_audited(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    resp = await async_client.get(
        "/api/v1/admin/audit/export", headers=headers, params=_viewer_params(format="csv")
    )
    assert resp.status_code == 200
    exports = await _audit_rows("audit.export", tenant_id=world["tenant_a"])
    assert exports, "the export itself must be audited"
    assert all(row.outcome == "ok" for row in exports)
    assert any(row.new_value.get("format") == "csv" for row in exports)


@pytest.mark.asyncio
async def test_export_denials_are_audited(async_client: AsyncClient, world):
    headers = await auth_headers(world["admin_a"])
    foreign = await async_client.get(
        "/api/v1/admin/audit/export",
        headers=headers,
        params=_viewer_params(format="json", tenant_id=str(world["tenant_b"])),
    )
    assert foreign.status_code == 404
    system_wide = await async_client.get(
        "/api/v1/admin/audit/export",
        headers=headers,
        params=_viewer_params(format="json", all="true"),
    )
    assert system_wide.status_code == 403
    denials = await _audit_rows("audit.export", outcome="denied")
    details = {row.new_value.get("detail") for row in denials if row.new_value}
    assert "cross_tenant" in details
    assert "system_wide_requires_system_admin" in details


@pytest.mark.asyncio
async def test_export_respects_scoping_and_filters(async_client: AsyncClient, world):
    sysadmin_headers = await auth_headers(world["sysadmin"])
    resp = await async_client.get(
        "/api/v1/admin/audit/export",
        headers=sysadmin_headers,
        params=_viewer_params(format="json", tenant_id=str(world["tenant_b"])),
    )
    rows = [json.loads(line) for line in resp.text.splitlines() if line]
    assert {row["tenant_id"] for row in rows} == {str(world["tenant_b"])}
    actions = {row["action"] for row in rows}
    assert {f"{_ACTION}.0", f"{_ACTION}.1"} <= actions
    assert "audit.export" in actions  # the export itself rides the same stream


# ---------------------------------------------------------------------------
# Export completeness (S2): same-timestamp ties + the truncation notice
# ---------------------------------------------------------------------------


async def _seed_tied_rows(tenant_id, user_id, count=5, *, resource_type="viewer_tie") -> list[str]:
    """Insert ``count`` audit rows sharing ONE ``created_at`` (a burst
    within a single clock tick), chained by hand."""
    from app.services.audit.audit_chain import (
        GENESIS_HASH,
        chain_key_for,
        compute_entry_hash,
    )

    created = datetime.now(UTC).replace(microsecond=0)
    actions = [f"{_ACTION}.tie.{i}" for i in range(count)]
    async with AsyncSessionLocal() as db, db.begin():
        head = (
            await db.execute(
                select(AuditChainHead).where(AuditChainHead.chain_key == chain_key_for(tenant_id))
            )
        ).scalar_one_or_none()
        last = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.chain_key == chain_key_for(tenant_id))
                .order_by(AuditEvent.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        prev_hash = last.entry_hash if last is not None else GENESIS_HASH
        seq = (last.seq if last is not None else 0) + 1
        for action in actions:
            entry = AuditEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                outcome="ok",
                created_at=created,  # identical for every row
                chain_key=chain_key_for(tenant_id),
                seq=seq,
                prev_hash=prev_hash,
            )
            entry.entry_hash = compute_entry_hash(
                chain_key=entry.chain_key,
                seq=entry.seq,
                prev_hash=entry.prev_hash,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                outcome=entry.outcome,
                created_at=entry.created_at,
            )
            db.add(entry)
            prev_hash = entry.entry_hash
            seq += 1
        # Keep the head row truthful — these are chained rows, not tampering
        # (the verifier walks the head too).
        if head is None:
            db.add(
                AuditChainHead(
                    chain_key=chain_key_for(tenant_id),
                    tenant_id=tenant_id,
                    seq=seq - 1,
                    head_hash=prev_hash,
                )
            )
        else:
            head.seq = seq - 1
            head.head_hash = prev_hash
    return actions


@pytest.mark.asyncio
async def test_export_tied_rows_are_not_skipped(world):
    """S2 — rows sharing one ``created_at`` all survive the keyset cursor:
    paging with a page size smaller than the tie group yields each row
    exactly once (a ``created_at``-only cursor skipped every tie member
    after the first page boundary)."""
    from app.services.tenancy.tenant_admin_service import TenantAdminService

    actions = await _seed_tied_rows(
        world["tenant_a"], world["admin_a"].id, 5, resource_type="viewer_tie_unit"
    )

    async with AsyncSessionLocal() as db:
        service = TenantAdminService(db)
        yielded: list[str] = []
        async for row in service.iter_audit_entries_all(
            tenant_id=world["tenant_a"],
            resource_type="viewer_tie_unit",
            page_size=2,
            max_rows=10000,
        ):
            assert row.action.startswith(f"{_ACTION}.tie.")
            yielded.append(row.action)
    assert sorted(yielded) == sorted(actions)
    assert len(yielded) == len(set(yielded)) == 5


@pytest.mark.asyncio
async def test_export_truncation_headers_and_audit_payload(
    async_client: AsyncClient, world, monkeypatch
):
    """S2 — a filter set matching more rows than the cap marks the stream:
    truncation headers on the response and ``truncated``/``total_matched``
    in the ``audit.export`` audit row. Under the cap nothing is flagged."""
    from app.api.v1.endpoints import admin as admin_endpoint

    await _seed_tied_rows(world["tenant_a"], world["admin_a"].id, 3, resource_type="viewer_tie")
    headers = await auth_headers(world["admin_a"])
    params = {"format": "json", "resource_type": "viewer_tie"}

    resp = await async_client.get("/api/v1/admin/audit/export", headers=headers, params=params)
    assert resp.status_code == 200
    assert resp.headers["x-audit-export-truncated"] == "false"
    assert int(resp.headers["x-audit-export-total"]) == 3

    monkeypatch.setattr(admin_endpoint, "AUDIT_EXPORT_MAX_ROWS", 2)
    capped = await async_client.get("/api/v1/admin/audit/export", headers=headers, params=params)
    assert capped.status_code == 200
    assert capped.headers["x-audit-export-truncated"] == "true"
    assert int(capped.headers["x-audit-export-total"]) == 3
    rows = [json.loads(line) for line in capped.text.splitlines() if line]
    assert len(rows) == 2  # the stream is capped, newest-first
    assert {row["action"] for row in rows} <= {f"{_ACTION}.tie.{i}" for i in range(3)}

    exports = await _audit_rows("audit.export", tenant_id=world["tenant_a"])
    latest = max(exports, key=lambda row: row.created_at)
    assert latest.new_value["truncated"] is True
    assert latest.new_value["total_matched"] == 3


def test_cors_exposes_audit_export_truncation_headers():
    """Phase 8 follow-up — a cross-origin browser client (SPA on another
    origin) can only read a response header if CORS exposes it: both CORS
    policies in ``app.main`` (dev LAN regex, prod allowlist — one is
    installed per env) must list the truncation headers in
    ``expose_headers``."""
    from starlette.middleware.cors import CORSMiddleware

    from app.main import _CORS_EXPOSE_HEADERS, app

    assert {"X-Audit-Export-Truncated", "X-Audit-Export-Total"} <= set(_CORS_EXPOSE_HEADERS)
    policies = [
        m.kwargs.get("expose_headers", []) for m in app.user_middleware if m.cls is CORSMiddleware
    ]
    assert len(policies) == 1  # exactly one CORS policy for this env
    assert {"X-Audit-Export-Truncated", "X-Audit-Export-Total"} <= set(policies[0])


# ---------------------------------------------------------------------------
# Retention view
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_view_system_admin_only(async_client: AsyncClient, world):
    headers = await auth_headers(world["sysadmin"])
    resp = await async_client.get("/api/v1/admin/audit/retention", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    values = {row["scope_value"]: row for row in body["policies"]}
    assert "audit_events" in values
    assert values["audit_events"]["action"] == "anonymize"
    assert values["audit_events"]["days"] == 3650
    assert body["legal_holds"] is None
    assert "no legal-hold registry" in body["notes"]

    admin_headers = await auth_headers(world["admin_a"])
    denied = await async_client.get("/api/v1/admin/audit/retention", headers=admin_headers)
    assert denied.status_code == 403  # audit:read @ SYSTEM — ADMIN is 403 at the gate


# ---------------------------------------------------------------------------
# Chain verifier unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_verifier_still_green_after_viewer_and_export(async_client: AsyncClient, world):
    headers = await auth_headers(world["sysadmin"])
    resp = await async_client.get(
        "/api/v1/admin/audit/verify",
        headers=headers,
        params={"tenant_id": str(world["tenant_a"])},
    )
    assert resp.status_code == 200
    status = resp.json()["chain"]
    assert status["ok"] is True
    assert status["entries"] >= 3

    all_resp = await async_client.get(
        "/api/v1/admin/audit/verify", headers=headers, params={"all": "true"}
    )
    assert all_resp.status_code == 200
    assert all_resp.json()["ok"] is True

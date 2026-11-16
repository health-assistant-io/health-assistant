"""Break-glass elevations — state machine, HTTP surface, consultation (ADR 0054
D1/D6, program Phase 12A).

Pins: the two-person law (requester ≠ approver, role per scope, single-admin
rejection), the held-permission subset rule, the consultation seam (one
time-boxed query per request, widening only the approved permission set and
never past the elevation's scope, expiry honored on the next request), the
audit row per transition **and per use**, notifications, the admin ledger
scoping (hidden-404 cross-tenant, system rows for SYSTEM_ADMIN) and the
expiry sweep.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.security import TokenData
from app.models.access_elevation import (
    AccessElevation,
    ElevationScope,
    ElevationStatus,
)
from app.models.audit_model import AuditEvent
from app.models.enums import Role
from app.services.auth.elevations import (
    MAX_DURATION_MINUTES,
    active_elevation_decision,
    approve_elevation,
    deny_elevation,
    expire_due_elevations,
    request_elevation,
    revoke_elevation,
)
from tests._auth_helpers import auth_headers, create_tenant, create_user, make_user_headers

TENANT_PERMISSIONS = ["audit:read"]


def _token(user) -> TokenData:
    return TokenData(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value if hasattr(user.role, "value") else user.role,
        sub=str(user.id),
    )


async def _request_row(
    *,
    requester,
    scope=ElevationScope.TENANT,
    permissions=TENANT_PERMISSIONS,
    status=ElevationStatus.PENDING,
    duration_minutes=30,
    tenant_id=None,
) -> AccessElevation:
    """A row crafted directly (lifecycle-edge tests), bypassing the service."""
    elevation = AccessElevation(
        tenant_id=tenant_id
        if tenant_id is not None
        else (None if scope is ElevationScope.SYSTEM else requester.tenant_id),
        requested_by=requester.id,
        scope=scope,
        status=status,
        permissions=permissions,
        reason="test window",
        duration_minutes=duration_minutes,
    )
    async with AsyncSessionLocal() as session:
        session.add(elevation)
        await session.commit()
        await session.refresh(elevation)
    return elevation


async def _rows_for(elevation_id) -> list[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditEvent.action).where(AuditEvent.resource_id == elevation_id)
        )
        return sorted(result.scalars().all())


async def _set_window(elevation_id, *, starts_at=None, expires_at=None) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AccessElevation)
            .where(AccessElevation.id == elevation_id)
            .values(starts_at=starts_at, expires_at=expires_at)
        )
        await session.commit()


async def _get_row(elevation_id) -> AccessElevation:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccessElevation).where(AccessElevation.id == elevation_id)
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Request rules
# ---------------------------------------------------------------------------


async def test_request_creates_pending_row_and_audits():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="ER outage, need audit review",
        )
    assert elevation.status is ElevationStatus.PENDING
    assert elevation.tenant_id == tenant_id
    assert elevation.duration_minutes == 30
    assert elevation.expires_at is None and elevation.approved_by is None
    actions = await _rows_for(elevation.id)
    assert actions == ["elevation_request"]


async def test_request_requires_reason_and_valid_duration_and_known_permissions():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValidationError):
            await request_elevation(
                db,
                actor=_token(admin),
                scope=ElevationScope.TENANT,
                permissions=TENANT_PERMISSIONS,
                duration_minutes=30,
                reason="   ",
            )
        with pytest.raises(ValidationError):
            await request_elevation(
                db,
                actor=_token(admin),
                scope=ElevationScope.TENANT,
                permissions=TENANT_PERMISSIONS,
                duration_minutes=MAX_DURATION_MINUTES + 1,
                reason="too long",
            )
        with pytest.raises(ValidationError):
            await request_elevation(
                db,
                actor=_token(admin),
                scope=ElevationScope.TENANT,
                permissions=["not:a-permission"],
                duration_minutes=30,
                reason="unknown permission",
            )


async def test_request_permissions_must_be_a_subset_of_held():
    tenant_id = await create_tenant()
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await request_elevation(
                db,
                actor=_token(manager),
                scope=ElevationScope.TENANT,
                permissions=["audit:read"],
                duration_minutes=30,
                reason="MANAGER does not hold audit:read",
            )
        with pytest.raises(AuthorizationError):
            await request_elevation(
                db,
                actor=_token(manager),
                scope=ElevationScope.TENANT,
                permissions=["tenant:create"],
                duration_minutes=30,
                reason="SYSTEM_ADMIN-only permission",
            )


async def test_system_scope_is_system_admin_request_only():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await request_elevation(
                db,
                actor=_token(admin),
                scope=ElevationScope.SYSTEM,
                permissions=["audit:read"],
                duration_minutes=30,
                reason="tenant admin cannot ask for the system door",
            )
        elevation = await request_elevation(
            db,
            actor=_token(sysadmin),
            scope=ElevationScope.SYSTEM,
            permissions=["source:manage"],
            duration_minutes=15,
            reason="cross-tenant containment",
        )
    assert elevation.tenant_id is None
    assert elevation.scope is ElevationScope.SYSTEM


async def test_second_live_request_for_same_scope_is_conflict():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="first",
        )
        with pytest.raises(ConflictError):
            await request_elevation(
                db,
                actor=_token(admin),
                scope=ElevationScope.TENANT,
                permissions=TENANT_PERMISSIONS,
                duration_minutes=30,
                reason="second",
            )


# ---------------------------------------------------------------------------
# Two-person approval
# ---------------------------------------------------------------------------


async def test_second_admin_approves_and_the_window_opens():
    tenant_id = await create_tenant()
    requester = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    approver = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(requester),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=45,
            reason="two-person flow",
        )
    async with AsyncSessionLocal() as db:
        approved = await approve_elevation(db, elevation.id, _token(approver))
    assert approved.status is ElevationStatus.ACTIVE
    assert approved.approved_by == approver.id
    assert approved.starts_at is not None
    assert approved.expires_at - approved.starts_at == timedelta(minutes=45)
    actions = await _rows_for(elevation.id)
    assert "elevation_approve" in actions


async def test_self_approval_is_denied_even_for_a_single_admin_tenant():
    """D6: the only ADMIN is the requester — nobody can approve, the row
    stays pending, and there is no bypass endpoint; recovery is the host
    rescue runbook."""
    tenant_id = await create_tenant()
    solo = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(solo),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="single admin",
        )
        with pytest.raises(AuthorizationError):
            await approve_elevation(db, elevation.id, _token(solo))
        with pytest.raises(AuthorizationError):
            await deny_elevation(db, elevation.id, _token(solo))
    row = await _get_row(elevation.id)
    assert row.status is ElevationStatus.PENDING


async def test_approver_role_must_match_the_elevation_scope():
    tenant_id = await create_tenant()
    admin_a = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    admin_b = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id)
    member_sysadmin = await create_user(role=Role.SYSTEM_ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        tenant_elevation = await request_elevation(
            db,
            actor=_token(admin_a),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="tenant window",
        )
        for actor in (manager, member_sysadmin):
            with pytest.raises(AuthorizationError):
                await approve_elevation(db, tenant_elevation.id, _token(actor))
        sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
        system_elevation = await request_elevation(
            db,
            actor=_token(sysadmin),
            scope=ElevationScope.SYSTEM,
            permissions=["source:manage"],
            duration_minutes=15,
            reason="system window",
        )
        sysadmin2 = await create_user(role=Role.SYSTEM_ADMIN)
        with pytest.raises(NotFoundError):
            await approve_elevation(db, system_elevation.id, _token(admin_b))
        approved = await approve_elevation(db, system_elevation.id, _token(sysadmin2))
    assert approved.status is ElevationStatus.ACTIVE


async def test_decisions_answer_hidden_404_across_tenants():
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    admin_a = await create_user(role=Role.ADMIN, tenant_id=tenant_a)
    admin_b = await create_user(role=Role.ADMIN, tenant_id=tenant_b)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin_a),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="tenant A window",
        )
    async with AsyncSessionLocal() as db:
        with pytest.raises(NotFoundError):
            await approve_elevation(db, elevation.id, _token(admin_b))


async def test_system_admin_can_contain_a_foreign_tenant_window():
    """Operator reach: a SYSTEM_ADMIN may revoke any tenant's window (D6
    containment) while remaining unable to APPROVE it (two-person law)."""
    tenant_a = await create_tenant()
    admin_a = await create_user(role=Role.ADMIN, tenant_id=tenant_a)
    admin_b = await create_user(role=Role.ADMIN, tenant_id=tenant_a)
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin_a),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="containment probe",
        )
        approved = await approve_elevation(db, elevation.id, _token(admin_b))
        with pytest.raises(AuthorizationError):
            await approve_elevation(db, elevation.id, _token(sysadmin))
        revoked = await revoke_elevation(db, approved.id, _token(sysadmin))
    assert revoked.status is ElevationStatus.REVOKED


# ---------------------------------------------------------------------------
# Consultation (the authorize() seam)
# ---------------------------------------------------------------------------


async def test_consultation_covers_only_the_requested_permissions():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=["audit:read", "settings:read"],
            duration_minutes=30,
            reason="coverage",
        )
        await approve_elevation(
            db, elevation.id, _token(await create_user(role=Role.ADMIN, tenant_id=tenant_id))
        )
        decision = await active_elevation_decision(
            db, user_id=admin.id, tenant_id=tenant_id, role=Role.ADMIN.value
        )
    assert decision is not None
    assert decision.covers("audit:read") and decision.covers("settings:read")
    assert not decision.covers("patient:read")
    assert decision.effective_scope().value == "tenant"


async def test_consultation_is_time_boxed_and_ignores_settled_rows():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    approver = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="time box",
        )
        await approve_elevation(db, elevation.id, _token(approver))

        decision = await active_elevation_decision(
            db, user_id=admin.id, tenant_id=tenant_id, role=Role.ADMIN.value
        )
        assert decision is not None

        past = datetime.now(UTC) - timedelta(minutes=1)
        await _set_window(elevation.id, expires_at=past)
        async with AsyncSessionLocal() as db:
            decision = await active_elevation_decision(
                db, user_id=admin.id, tenant_id=tenant_id, role=Role.ADMIN.value
            )
        assert decision is None

    revoked = await _request_row(requester=admin, status=ElevationStatus.REVOKED)
    async with AsyncSessionLocal() as db:
        decision = await active_elevation_decision(
            db, user_id=admin.id, tenant_id=tenant_id, role=Role.ADMIN.value
        )
    assert decision is None
    assert revoked.status is ElevationStatus.REVOKED


async def test_system_elevation_consulted_only_for_system_admin():
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    elevation = await _request_row(
        requester=sysadmin,
        scope=ElevationScope.SYSTEM,
        permissions=["source:manage"],
        status=ElevationStatus.ACTIVE,
    )
    await _set_window(
        elevation.id,
        starts_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    async with AsyncSessionLocal() as db:
        decision = await active_elevation_decision(
            db, user_id=sysadmin.id, tenant_id=sysadmin.tenant_id, role=Role.SYSTEM_ADMIN.value
        )
        assert decision is not None and decision.effective_scope().value == "system"
        someone_else = await create_user(role=Role.ADMIN)
        decision = await active_elevation_decision(
            db,
            user_id=someone_else.id,
            tenant_id=someone_else.tenant_id,
            role=Role.ADMIN.value,
        )
        assert decision is None


async def test_tenant_elevation_does_not_follow_a_foreign_tenant():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    approver = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="own tenant only",
        )
        await approve_elevation(db, elevation.id, _token(approver))
    other_tenant = await create_tenant()
    async with AsyncSessionLocal() as db:
        decision = await active_elevation_decision(
            db, user_id=admin.id, tenant_id=other_tenant, role=Role.ADMIN.value
        )
    assert decision is None


# ---------------------------------------------------------------------------
# Expiry sweep + revoke
# ---------------------------------------------------------------------------


async def test_expiry_sweep_flips_due_rows_audits_and_notifies():
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    approver = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(admin),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="expiry sweep",
        )
        await approve_elevation(db, elevation.id, _token(approver))
    await _set_window(elevation.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    async with AsyncSessionLocal() as db:
        flipped = await expire_due_elevations(db)
    assert flipped >= 1
    row = await _get_row(elevation.id)
    assert row.status is ElevationStatus.EXPIRED
    assert "elevation_expire" in await _rows_for(elevation.id)


async def test_requester_and_eligible_approvers_can_revoke():
    tenant_id = await create_tenant()
    requester = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    approver = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id)
    async with AsyncSessionLocal() as db:
        elevation = await request_elevation(
            db,
            actor=_token(requester),
            scope=ElevationScope.TENANT,
            permissions=TENANT_PERMISSIONS,
            duration_minutes=30,
            reason="revoke path",
        )
        approved = await approve_elevation(db, elevation.id, _token(approver))
    async with AsyncSessionLocal() as db:
        revoked = await revoke_elevation(db, approved.id, _token(requester))
    assert revoked.status is ElevationStatus.REVOKED
    assert revoked.revoked_at is not None

    with pytest.raises(AuthorizationError):
        async with AsyncSessionLocal() as db:
            await revoke_elevation(db, approved.id, _token(manager))

    async with AsyncSessionLocal() as db:
        own = await request_elevation(
            db,
            actor=_token(manager),
            scope=ElevationScope.TENANT,
            permissions=["integration:connect"],
            duration_minutes=30,
            reason="manager revokes own pending",
        )
        self_revoked = await revoke_elevation(db, own.id, _token(manager))
    assert self_revoked.status is ElevationStatus.REVOKED
    assert "elevation_revoke" in await _rows_for(own.id)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


async def test_http_request_and_list_flow(async_client: AsyncClient):
    tenant_id = await create_tenant()
    admin_a, headers_a = await make_user_headers(step_up=True, role=Role.ADMIN, tenant_id=tenant_id)
    _admin_b, headers_b = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    response = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=headers_a,
        json={
            "scope": "tenant",
            "permissions": ["audit:read"],
            "duration_minutes": 30,
            "reason": "ER outage",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["requested_by"] == str(admin_a.id)
    elevation_id = body["id"]

    listed = await async_client.get("/api/v1/admin/access-elevations", headers=headers_b)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == elevation_id

    approved = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=headers_b
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    assert approved.json()["approved_by"] == str(_admin_b.id)


async def test_http_user_is_gate_denied_and_manager_cannot_approve(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _manager, manager_headers = await make_user_headers(
        step_up=True, role=Role.MANAGER, tenant_id=tenant_id
    )
    _user, user_headers = await make_user_headers(step_up=True, role=Role.USER, tenant_id=tenant_id)
    _admin_b, admin_b_headers = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    response = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=user_headers,
        json={
            "scope": "tenant",
            "permissions": ["patient:read"],
            "duration_minutes": 30,
            "reason": "USER has no break_glass:request",
        },
    )
    assert response.status_code == 403

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=manager_headers,
        json={
            "scope": "tenant",
            "permissions": ["integration:connect"],
            "duration_minutes": 30,
            "reason": "manager request",
        },
    )
    assert created.status_code == 201
    elevation_id = created.json()["id"]

    approved = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve",
        headers=manager_headers,
    )
    assert approved.status_code == 403
    row = await _get_row(uuid.UUID(elevation_id))
    assert row.status is ElevationStatus.PENDING
    assert "elevation_approve_denied" in await _rows_for(row.id), (
        "a refused approval left no audit row"
    )

    approved = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve",
        headers=admin_b_headers,
    )
    assert approved.status_code == 200


async def test_http_cross_tenant_list_answers_hidden_404(async_client: AsyncClient):
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    _admin_a, headers_a = await make_user_headers(step_up=True, role=Role.ADMIN, tenant_id=tenant_a)
    _admin_b, _headers_b = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_b
    )

    foreign = await async_client.get(
        "/api/v1/admin/access-elevations", params={"tenant_id": str(tenant_b)}, headers=headers_a
    )
    assert foreign.status_code == 404

    response = await async_client.get(
        "/api/v1/admin/access-elevations", params={"scope": "system"}, headers=headers_a
    )
    assert response.status_code == 403

    _sysadmin, sys_headers = await make_user_headers(step_up=True, role=Role.SYSTEM_ADMIN)
    system_view = await async_client.get(
        "/api/v1/admin/access-elevations", params={"scope": "system"}, headers=sys_headers
    )
    assert system_view.status_code == 200
    assert all(item["tenant_id"] is None for item in system_view.json()["items"]), (
        "scope=system returned tenant rows"
    )


async def test_http_system_rows_are_system_admin_only(async_client: AsyncClient):
    _sysadmin, sys_headers = await make_user_headers(step_up=True, role=Role.SYSTEM_ADMIN)
    _sysadmin2, sys2_headers = await make_user_headers(step_up=True, role=Role.SYSTEM_ADMIN)

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=sys_headers,
        json={
            "scope": "system",
            "permissions": ["source:manage"],
            "duration_minutes": 15,
            "reason": "instance containment",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["tenant_id"] is None
    elevation_id = created.json()["id"]

    tenant_admin = await create_user(role=Role.ADMIN)
    tenant_headers = await auth_headers(tenant_admin, step_up=True)
    invisible = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=tenant_headers
    )
    assert invisible.status_code == 404
    assert "elevation_approve_denied" in await _rows_for(uuid.UUID(elevation_id)), (
        "a hidden cross-tenant refusal left no audit row"
    )

    approved = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=sys2_headers
    )
    assert approved.status_code == 200


async def test_http_use_is_audited_while_active_and_not_after_expiry(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _admin, headers = await make_user_headers(step_up=True, role=Role.ADMIN, tenant_id=tenant_id)
    _approver, approver_headers = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=headers,
        json={
            "scope": "tenant",
            "permissions": ["audit:read"],
            "duration_minutes": 30,
            "reason": "use-audit pin",
        },
    )
    elevation_id = uuid.UUID(created.json()["id"])
    await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=approver_headers
    )

    response = await async_client.get("/api/v1/admin/audit", headers=headers)
    assert response.status_code == 200
    actions = await _rows_for(elevation_id)
    assert actions.count("elevation_use") == 1

    await _set_window(elevation_id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    await async_client.get("/api/v1/admin/audit", headers=headers)
    actions = await _rows_for(elevation_id)
    assert actions.count("elevation_use") == 1


async def test_http_tenant_elevation_never_opens_system_surfaces(async_client: AsyncClient):
    """A tenant window widens to tenant reach — a SYSTEM-scoped route stays
    denied even when the elevation covers the permission."""
    tenant_id = await create_tenant()
    _admin, headers = await make_user_headers(step_up=True, role=Role.ADMIN, tenant_id=tenant_id)
    _approver, approver_headers = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=headers,
        json={
            "scope": "tenant",
            "permissions": ["settings:read"],
            "duration_minutes": 30,
            "reason": "system-surface probe",
        },
    )
    elevation_id = uuid.UUID(created.json()["id"])
    await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=approver_headers
    )

    response = await async_client.get("/api/v1/settings/system", headers=headers)
    assert response.status_code == 403
    assert (await _rows_for(elevation_id)).count("elevation_use") == 0


async def test_me_elevations_lists_only_the_callers_rows(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _admin_a, headers_a = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )
    _admin_b, headers_b = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=headers_a,
        json={
            "scope": "tenant",
            "permissions": ["audit:read"],
            "duration_minutes": 30,
            "reason": "mine",
        },
    )
    mine = await async_client.get("/api/v1/me/elevations", headers=headers_a)
    assert mine.status_code == 200
    ids = {item["id"] for item in mine.json()}
    assert created.json()["id"] in ids

    theirs = await async_client.get("/api/v1/me/elevations", headers=headers_b)
    assert theirs.status_code == 200
    assert created.json()["id"] not in {item["id"] for item in theirs.json()}


async def test_http_deny_and_revoked_paths(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _admin_a, headers_a = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )
    _admin_b, headers_b = await make_user_headers(
        step_up=True, role=Role.ADMIN, tenant_id=tenant_id
    )

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        headers=headers_a,
        json={
            "scope": "tenant",
            "permissions": ["audit:read"],
            "duration_minutes": 30,
            "reason": "deny path",
        },
    )
    elevation_id = created.json()["id"]
    denied = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/deny",
        headers=headers_b,
        json={"reason": "not convinced"},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"

    again = await async_client.post(
        f"/api/v1/admin/access-elevations/{elevation_id}/approve", headers=headers_b
    )
    assert again.status_code == 409

    actions = await _rows_for(uuid.UUID(elevation_id))
    assert "elevation_deny" in actions
    assert "elevation_approve_denied" in actions, "a settled-row refusal left no audit row"


async def test_scoped_credential_cannot_request_or_approve_break_glass():
    """F7: break-glass is a human act — a scoped credential is refused.

    A PAT/integration principal carries its owner's role name; without this
    wall it could open (or approve) a time-boxed widening of that role while
    the owner is absent, and get it consulted by ``authorize()``. Both
    transitions answer the same 403 as any other authorization failure.
    """
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    scoped = TokenData(
        user_id=admin.id,
        tenant_id=tenant_id,
        role=Role.ADMIN.value,
        sub=str(admin.id),
        pat_id=str(uuid.uuid4()),
        pat_permissions=["break_glass:request", "audit:read"],
        pat_patient_scope="all",
    )

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await request_elevation(
                db,
                actor=scoped,
                scope=ElevationScope.TENANT,
                permissions=TENANT_PERMISSIONS,
                duration_minutes=30,
                reason="scoped principal must not open a window",
            )

    pending = await _request_row(requester=admin)
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await approve_elevation(db, pending.id, scoped)

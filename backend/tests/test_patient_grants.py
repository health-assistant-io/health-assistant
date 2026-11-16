"""The grant state machine + grant-aware access helpers (ADR 0054 Phase 2).

Service level: per-source creation rules (staff immediate / patient pending
/ guardian ownerless), the doctor-must-have-a-user law, identity dedupe,
accept/decline/revoke transitions, expiry, and the tenant knobs
(``manager_scope`` / ``allow_patient_share`` read per request).
Access level: ``check_patient_access`` permission ranking (read < write <
admin), expired/revoked grants ignored, cross-tenant hidden-404, in-tenant
foreign 403, and ``accessible_patient_ids`` per role/scope.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.services.auth.patient_grants as grants_module
from app.core.database import AsyncSessionLocal
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import TokenData
from app.models.audit_model import AuditEvent
from app.models.doctor_model import DoctorModel
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.notification import Notification, NotificationRecipient
from app.models.patient_access_grant import (
    GrantPermission,
    GrantSource,
    GrantStatus,
    PatientAccessGrant,
)
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.user import TokenData as _TokenData  # noqa: F401
from app.services.auth.access import (
    accessible_patient_ids,
    check_patient_access,
    patient_scope_condition,
)
from app.services.auth.patient_grants import (
    accept_grant,
    create_grant,
    decline_grant,
    expire_due_grants,
    grants_for_grantee,
    normalize_allow_patient_share,
    normalize_manager_scope,
    revoke_grant,
    tenant_access_settings,
    update_grant,
)
from tests._auth_helpers import create_tenant, create_user


async def _token(user, tenant_id=None) -> TokenData:
    return TokenData(
        user_id=user.id,
        tenant_id=tenant_id or user.tenant_id,
        role=user.role.value if hasattr(user.role, "value") else user.role,
        sub=str(user.id),
    )


async def _make_patient(tenant_id, user_id=None) -> Patient:
    async with AsyncSessionLocal() as session:
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            name={"family": "Grant", "given": ["Test"]},
            gender="UNKNOWN",
        )
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
    return patient


async def _set_tenant_settings(tenant_id, settings: dict) -> None:
    async with AsyncSessionLocal() as session:
        tenant = await session.get(TenantModel, tenant_id)
        tenant.settings = settings
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(tenant, "settings")
        await session.commit()


@pytest.fixture
async def world():
    """One tenant: an owner USER, their patient, a staff MANAGER+ADMIN and a doctor."""
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id)
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    second = await create_user(role=Role.USER, tenant_id=tenant_id)
    async with AsyncSessionLocal() as session:
        doctor = DoctorModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=manager.id,
            name="Dr linked",
        )
        unlinked = DoctorModel(id=uuid.uuid4(), tenant_id=tenant_id, user_id=None, name="Dr ghost")
        session.add_all([doctor, unlinked])
        await session.commit()
        await session.refresh(doctor)
        await session.refresh(unlinked)
    patient = await _make_patient(tenant_id, owner.id)
    return {
        "tenant_id": tenant_id,
        "owner": owner,
        "manager": manager,
        "admin": admin,
        "second": second,
        "doctor": doctor,
        "unlinked": unlinked,
        "patient": patient,
    }


# ---------------------------------------------------------------------------
# Tenant settings normalization
# ---------------------------------------------------------------------------


def test_normalize_manager_scope():
    assert normalize_manager_scope("assigned") == "assigned"
    assert normalize_manager_scope("tenant") == "tenant"
    assert normalize_manager_scope(None) == "tenant"
    assert normalize_manager_scope("everything") == "tenant"


def test_normalize_allow_patient_share():
    assert normalize_allow_patient_share(None) is True
    assert normalize_allow_patient_share(True) is True
    assert normalize_allow_patient_share(False) is False
    assert normalize_allow_patient_share("false") is False
    assert normalize_allow_patient_share("off") is False
    assert normalize_allow_patient_share("yes") is True


@pytest.mark.asyncio
async def test_tenant_access_settings_read_per_request(world):
    await _set_tenant_settings(
        world["tenant_id"], {"manager_scope": "assigned", "allow_patient_share": False}
    )
    settings = await tenant_access_settings(AsyncSessionLocal(), world["tenant_id"])
    assert settings.manager_scope == "assigned"
    assert settings.allow_patient_share is False
    await _set_tenant_settings(world["tenant_id"], {})
    settings = await tenant_access_settings(AsyncSessionLocal(), world["tenant_id"])
    assert settings.manager_scope == "tenant"
    assert settings.allow_patient_share is True


# ---------------------------------------------------------------------------
# Creation rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_grant_is_active_immediately(world):
    token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
        assert grant.status is GrantStatus.ACTIVE
        assert grant.accepted_at is not None


@pytest.mark.asyncio
async def test_user_cannot_use_admin_source(world):
    token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=token,
                permission="read",
                grantee_user_id=world["manager"].id,
                source="admin",
            )


@pytest.mark.asyncio
async def test_patient_share_creates_pending_and_staff_accepts(world):
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        assert grant.status is GrantStatus.PENDING
        accepted = await accept_grant(db, grant.id, await _token(world["manager"]))
        assert accepted.status is GrantStatus.ACTIVE
        assert accepted.accepted_at is not None


@pytest.mark.asyncio
async def test_patient_share_decline(world):
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        declined = await decline_grant(
            db, grant.id, await _token(world["manager"]), reason="not my patient"
        )
        assert declined.status is GrantStatus.DECLINED
        assert declined.reason == "not my patient"


@pytest.mark.asyncio
async def test_only_grantee_can_accept(world):
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        with pytest.raises(AuthorizationError):
            await accept_grant(db, grant.id, await _token(world["second"]))


@pytest.mark.asyncio
async def test_patient_share_disabled_by_tenant(world):
    await _set_tenant_settings(world["tenant_id"], {"allow_patient_share": False})
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=owner_token,
                permission="read",
                grantee_user_id=world["manager"].id,
                source="patient",
            )


@pytest.mark.asyncio
async def test_patient_cannot_share_via_doctor_without_user(world):
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValidationError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=owner_token,
                permission="read",
                grantee_doctor_id=world["unlinked"].id,
                source="patient",
            )


@pytest.mark.asyncio
async def test_guardian_grant_requires_ownerless_patient_and_admin(world):
    orphan = await _make_patient(world["tenant_id"], None)
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=orphan,
            actor=await _token(world["admin"]),
            permission="read",
            grantee_user_id=world["second"].id,
            source="guardian",
        )
        assert grant.status is GrantStatus.ACTIVE

        with pytest.raises(ValidationError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=await _token(world["admin"]),
                permission="read",
                grantee_user_id=world["second"].id,
                source="guardian",
            )
        with pytest.raises(AuthorizationError):
            await create_grant(
                db,
                patient=orphan,
                actor=await _token(world["manager"]),
                permission="read",
                grantee_user_id=world["second"].id,
                source="guardian",
            )


@pytest.mark.asyncio
async def test_duplicate_identity_rejected_across_user_and_doctor_grantees(world):
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
        with pytest.raises(ConflictError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=admin_token,
                permission="read",
                grantee_doctor_id=world["doctor"].id,
                source="admin",
            )


@pytest.mark.asyncio
async def test_expiry_must_be_future_and_revoked_slot_reusable(world):
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValidationError):
            await create_grant(
                db,
                patient=world["patient"],
                actor=admin_token,
                permission="read",
                grantee_user_id=world["manager"].id,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
        )
        await revoke_grant(db, grant.id, admin_token)
        again = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="write",
            grantee_user_id=world["manager"].id,
        )
        assert again.status is GrantStatus.ACTIVE
        assert again.permission is GrantPermission.WRITE


@pytest.mark.asyncio
async def test_update_and_expire(world):
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
        )
        updated = await update_grant(
            db,
            grant.id,
            admin_token,
            permission="write",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        assert updated.permission is GrantPermission.WRITE
        assert updated.expires_at is not None

        past = datetime.now(UTC) - timedelta(minutes=2)
        async with AsyncSessionLocal() as raw:
            from sqlalchemy import update as sa_update

            await raw.execute(
                sa_update(type(grant)).where(type(grant).id == grant.id).values(expires_at=past)
            )
            await raw.commit()

        assert await expire_due_grants(AsyncSessionLocal()) >= 1
        async with AsyncSessionLocal() as raw:
            from app.models.patient_access_grant import PatientAccessGrant as G

            row = (
                await raw.execute(__import__("sqlalchemy").select(G).where(G.id == grant.id))
            ).scalar_one()
            assert row.status is GrantStatus.EXPIRED


@pytest.mark.asyncio
async def test_owner_can_revoke_own_share_but_not_admin_grants(world):
    owner_token = await _token(world["owner"])
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        await revoke_grant(db, share.id, owner_token)
        admin_grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
        with pytest.raises(AuthorizationError):
            await revoke_grant(db, admin_grant.id, owner_token)
        await revoke_grant(db, admin_grant.id, admin_token)


@pytest.mark.asyncio
async def test_pending_share_revoke_notifies_no_accept(world):
    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        revoked = await revoke_grant(db, share.id, owner_token)
        assert revoked.status is GrantStatus.REVOKED
        rows = await grants_for_grantee(
            AsyncSessionLocal(),
            world["tenant_id"],
            world["manager"].id,
            GrantStatus.PENDING,
        )
        assert all(row.id != share.id for row in rows)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accessible_ids_per_role(world):
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        assert await accessible_patient_ids(admin_token, db) is None
        assert await accessible_patient_ids(await _token(world["manager"]), db) is None

    await _set_tenant_settings(world["tenant_id"], {"manager_scope": "assigned"})
    async with AsyncSessionLocal() as db:
        manager_ids = await accessible_patient_ids(await _token(world["manager"]), db)
        assert manager_ids == set()
        await create_grant(
            db,
            patient=world["patient"],
            actor=await _token(world["admin"]),
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
        manager_ids = await accessible_patient_ids(await _token(world["manager"]), db)
        assert world["patient"].id in manager_ids

    # flip back to tenant: None again (mid-session, no re-login)
    await _set_tenant_settings(world["tenant_id"], {})
    async with AsyncSessionLocal() as db:
        assert await accessible_patient_ids(await _token(world["manager"]), db) is None


@pytest.mark.asyncio
async def test_check_patient_access_grant_ranking(world):
    """Grant ranking on a grant-only reach: an ownerless patient guardian-granted
    to a USER account (the only legal grantee shape for a non-staff login)."""
    orphan = await _make_patient(world["tenant_id"], None)
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=orphan,
            actor=admin_token,
            permission="read",
            grantee_user_id=world["second"].id,
            source="guardian",
        )
        second_token = await _token(world["second"])
        patient = await check_patient_access(orphan.id, second_token, db)
        assert patient.id == orphan.id
        await check_patient_access(orphan.id, second_token, db, "read")
        with pytest.raises(AuthorizationError):
            await check_patient_access(orphan.id, second_token, db, "write")
        with pytest.raises(AuthorizationError):
            await check_patient_access(orphan.id, second_token, db, "admin")

        from sqlalchemy import update as sa_update

        await db.execute(
            sa_update(type(grant)).where(type(grant).id == grant.id).values(permission="write")
        )
        await db.commit()
        await check_patient_access(orphan.id, second_token, db, "write")
        with pytest.raises(AuthorizationError):
            await check_patient_access(orphan.id, second_token, db, "admin")

        await db.execute(
            sa_update(type(grant)).where(type(grant).id == grant.id).values(permission="admin")
        )
        await db.commit()
        await check_patient_access(orphan.id, second_token, db, "admin")

        await revoke_grant(db, grant.id, admin_token)
        with pytest.raises(AuthorizationError):
            await check_patient_access(orphan.id, second_token, db)


@pytest.mark.asyncio
async def test_expired_grant_is_ignored(world):
    orphan = await _make_patient(world["tenant_id"], None)
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=orphan,
            actor=await _token(world["admin"]),
            permission="read",
            grantee_user_id=world["second"].id,
            source="guardian",
        )
        from sqlalchemy import update as sa_update

        await db.execute(
            sa_update(type(grant))
            .where(type(grant).id == grant.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        with pytest.raises(AuthorizationError):
            await check_patient_access(orphan.id, await _token(world["second"]), db)


@pytest.mark.asyncio
async def test_cross_tenant_hidden_404_and_foreign_403(world):
    other_tenant = await create_tenant()
    other_owner = await create_user(role=Role.USER, tenant_id=other_tenant)
    foreign_patient = await _make_patient(other_tenant, other_owner.id)

    owner_token = await _token(world["owner"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(NotFoundError):
            await check_patient_access(foreign_patient.id, owner_token, db)
        with pytest.raises(AuthorizationError):
            await check_patient_access(world["patient"].id, await _token(world["second"]), db)


@pytest.mark.asyncio
async def test_patient_scope_condition_shapes(world):
    from sqlalchemy import false, true

    col = Patient.user_id  # any column works for the shape check
    assert patient_scope_condition(col, None) is None
    assert str(patient_scope_condition(col, set())) == str(false())
    clause = patient_scope_condition(col, {uuid.uuid4()})
    assert clause is not None
    _ = true()


# ---------------------------------------------------------------------------
# Audit trail (B1) — every transition writes one audit_events row
# ---------------------------------------------------------------------------


async def _audit_rows(action: str, resource_id=None) -> list[AuditEvent]:
    query = select(AuditEvent).where(
        AuditEvent.action == action,
        AuditEvent.resource_type == "PatientAccessGrant",
    )
    if resource_id is not None:
        query = query.where(AuditEvent.resource_id == resource_id)
    query = query.order_by(AuditEvent.created_at.asc())
    async with AsyncSessionLocal() as session:
        return list((await session.execute(query)).scalars().all())


@pytest.mark.asyncio
async def test_create_and_accept_write_audit_rows(world):
    """B1: ``grant_create`` and ``grant_accept`` each leave one row —
    actor, action, resource, outcome ok, id-shaped payload (no PHI)."""
    admin_token = await _token(world["admin"])
    owner_token = await _token(world["owner"])
    manager_token = await _token(world["manager"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
    [row] = await _audit_rows("grant_create", grant.id)
    assert str(row.user_id) == str(world["admin"].id)
    assert str(row.resource_id) == str(grant.id)
    assert str(row.tenant_id) == str(world["tenant_id"])
    assert row.outcome == "ok"
    assert row.new_value["patient_id"] == str(grant.patient_id)
    assert row.new_value["status"] == "active"

    # One live/pending grant per identity per patient, so the share under
    # accept lands on a second patient.
    orphan = await _make_patient(world["tenant_id"], world["owner"].id)
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=orphan,
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        await accept_grant(db, share.id, manager_token)
    [row] = await _audit_rows("grant_accept", share.id)
    assert str(row.user_id) == str(world["manager"].id)
    assert str(row.resource_id) == str(share.id)
    assert row.outcome == "ok"
    assert row.new_value["status"] == "active"


@pytest.mark.asyncio
async def test_decline_revoke_update_write_audit_rows_without_free_text(world):
    """B1: decline/revoke/update audit rows exist and carry no free-text
    reason — the payload stays id-shaped (the update records that the reason
    changed, never its content)."""
    owner_token = await _token(world["owner"])
    manager_token = await _token(world["manager"])
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
        await decline_grant(db, share.id, manager_token, reason="not my patient")
    [row] = await _audit_rows("grant_decline", share.id)
    assert str(row.user_id) == str(world["manager"].id)
    assert row.outcome == "ok"
    assert "not my patient" not in json.dumps(row.new_value)
    assert "reason" not in row.new_value

    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
            reason="covering Dr Manager's leave",
        )
        await update_grant(
            db,
            grant.id,
            admin_token,
            permission="write",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            reason="now covering nights too",
        )
        await revoke_grant(db, grant.id, admin_token)
    [update_row] = await _audit_rows("grant_update", grant.id)
    assert str(update_row.user_id) == str(world["admin"].id)
    assert update_row.new_value["permission"] == "write"
    assert update_row.new_value.get("reason_updated") is True
    assert update_row.new_value.get("expires_at") is not None
    payload_text = json.dumps(update_row.new_value)
    assert "now covering nights too" not in payload_text
    assert "covering Dr Manager's leave" not in payload_text
    [revoke_row] = await _audit_rows("grant_revoke", grant.id)
    assert str(revoke_row.user_id) == str(world["admin"].id)
    assert revoke_row.outcome == "ok"
    assert revoke_row.new_value["status"] == "revoked"


# ---------------------------------------------------------------------------
# Accept-time re-validation (S2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_revalidates_staff_role_and_share_flag(world):
    """A pending share is judged by the rules at accept time: a grantee
    demoted to USER answers 403, a tenant that switched
    ``allow_patient_share`` off answers 409."""
    owner_token = await _token(world["owner"])
    manager_token = await _token(world["manager"])
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, world["manager"].id)
        user.role = Role.USER
        await session.commit()
    with pytest.raises(AuthorizationError):
        await accept_grant(AsyncSessionLocal(), share.id, manager_token)

    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, world["manager"].id)
        user.role = Role.MANAGER
        await session.commit()
    await _set_tenant_settings(world["tenant_id"], {"allow_patient_share": False})
    with pytest.raises(ConflictError):
        await accept_grant(AsyncSessionLocal(), share.id, manager_token)
    await _set_tenant_settings(world["tenant_id"], {})


# ---------------------------------------------------------------------------
# Duplicate races map to 409 (S3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_duplicate_accept_maps_to_conflict(world):
    """S3: the partial unique index catches the accept race — an active row
    landing between the share's creation and its acceptance turns the loser's
    accept into ConflictError (409), not a raw IntegrityError."""
    owner_token = await _token(world["owner"])
    manager_token = await _token(world["manager"])
    async with AsyncSessionLocal() as db:
        share = await create_grant(
            db,
            patient=world["patient"],
            actor=owner_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="patient",
        )
    winner = PatientAccessGrant(
        tenant_id=world["tenant_id"],
        patient_id=world["patient"].id,
        grantee_user_id=world["manager"].id,
        permission=GrantPermission.READ,
        source=GrantSource.ADMIN,
        status=GrantStatus.ACTIVE,
        granted_by=world["admin"].id,
    )
    async with AsyncSessionLocal() as session:
        session.add(winner)
        await session.commit()
    with pytest.raises(ConflictError):
        await accept_grant(AsyncSessionLocal(), share.id, manager_token)
    async with AsyncSessionLocal() as session:
        row = await session.get(PatientAccessGrant, winner.id)
        await session.delete(row)
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_duplicate_create_maps_to_conflict(world, monkeypatch):
    """S3: when the app-level dedupe misses the check→insert race window,
    the partial unique index still fires and ``create_grant`` maps it to
    ConflictError (409) with a clear detail."""

    async def _no_dedupe(*args, **kwargs):
        return None

    monkeypatch.setattr(grants_module, "_reject_duplicate_grantee", _no_dedupe)
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        await grants_module.create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
    with pytest.raises(ConflictError, match="already holds a grant"):
        async with AsyncSessionLocal() as db:
            await grants_module.create_grant(
                db,
                patient=world["patient"],
                actor=admin_token,
                permission="read",
                grantee_user_id=world["manager"].id,
                source="admin",
            )


# ---------------------------------------------------------------------------
# Expiry sweep (S1) — audit + notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_due_grants_audits_and_notifies(world):
    """S1: the sweep flips due actives, audits each flip (``grant_expire``,
    system actor) and notifies the grantee and the grantor."""
    admin_token = await _token(world["admin"])
    async with AsyncSessionLocal() as db:
        grant = await create_grant(
            db,
            patient=world["patient"],
            actor=admin_token,
            permission="read",
            grantee_user_id=world["manager"].id,
            source="admin",
        )
        past = datetime.now(UTC) - timedelta(minutes=1)
        from sqlalchemy import update as sa_update

        await db.execute(
            sa_update(type(grant)).where(type(grant).id == grant.id).values(expires_at=past)
        )
        await db.commit()

    flipped = await expire_due_grants(AsyncSessionLocal())
    assert flipped >= 1

    async with AsyncSessionLocal() as session:
        row = await session.get(PatientAccessGrant, grant.id)
        assert row.status is GrantStatus.EXPIRED

    [audit_row] = await _audit_rows("grant_expire", grant.id)
    assert audit_row.user_id is None
    assert str(audit_row.resource_id) == str(grant.id)
    assert audit_row.outcome == "ok"
    assert audit_row.new_value["status"] == "expired"

    async with AsyncSessionLocal() as session:
        notified = (
            (
                await session.execute(
                    select(NotificationRecipient.user_id)
                    .join(Notification, NotificationRecipient.notification_id == Notification.id)
                    .where(Notification.source_ref["grant_id"].astext == str(grant.id))
                )
            )
            .scalars()
            .all()
        )
    assert {str(u) for u in notified} == {str(world["manager"].id), str(world["admin"].id)}

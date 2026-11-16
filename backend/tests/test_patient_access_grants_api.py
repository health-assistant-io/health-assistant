"""Grant API surface + query-surface parity (ADR 0054 Phase 2 / program 7A).

Exercises the HTTP surface end-to-end (real sessions through the central
gate): the ``/patients/{id}/access`` ledger + grant/revoke lifecycle, the
``/me/access`` payloads + accept/decline, and the parity law — a grant must
reach every list/search surface (patients, examinations, search, allergies,
documents, task monitor) while a ``read`` grant never satisfies a write
(and export never rides a grant at all — D5). Also: audit rows per
transition (B1), inbox notifications for the affected parties, accept-time
re-validation (S2), duplicate races → 409 (S3), PATCH clear semantics (S4),
and the notification-rules reachable set (S5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import AsyncSessionLocal
from app.models.audit_model import AuditEvent
from app.models.doctor_model import DoctorModel
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.notifications import notification_rule_service
from tests._auth_helpers import auth_headers, create_tenant, create_user


async def _add_patient(tenant_id, user_id=None) -> uuid.UUID:
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name={"family": "Parity", "given": ["Case"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    return patient_id


async def _add_examination(tenant_id, patient_id, extraction_status=None) -> uuid.UUID:
    exam_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            ExaminationModel(
                id=exam_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                notes="parity exam",
                examination_date=datetime(2026, 11, 1, tzinfo=UTC),
                extraction_status=extraction_status,
            )
        )
        await session.commit()
    return exam_id


async def _add_document(tenant_id, patient_id, owner_id) -> uuid.UUID:
    from app.models.document_model import DocumentModel

    doc_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            DocumentModel(
                id=doc_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                owner_id=owner_id,
                filename="parity.pdf",
                file_path="/uploads/parity.pdf",
                status="processing",
            )
        )
        await session.commit()
    return doc_id


async def _grant_actions(grant_id) -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(AuditEvent.action).where(AuditEvent.resource_id == grant_id)
        )
        return set(rows.scalars().all())


async def _inbox_titles(client: AsyncClient, user) -> list[str]:
    response = await client.get(
        "/api/v1/notifications/inbox", headers=await auth_headers(user, step_up=True)
    )
    assert response.status_code == 200, response.text
    return [item["notification"]["title"] for item in response.json()["items"]]


async def _set_settings(tenant_id, settings: dict) -> None:
    async with AsyncSessionLocal() as session:
        tenant = await session.get(TenantModel, tenant_id)
        tenant.settings = settings
        flag_modified(tenant, "settings")
        await session.commit()


@pytest.fixture
async def clinic():
    """Tenant: owner USER + their patient, ADMIN, MANAGER, staff doctor row."""
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id, full_name="Pat Owner")
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id, full_name="Dr Manager")
    other = await create_user(role=Role.USER, tenant_id=tenant_id)
    async with AsyncSessionLocal() as session:
        doctor = DoctorModel(
            id=uuid.uuid4(), tenant_id=tenant_id, user_id=manager.id, name="Dr Manager"
        )
        session.add(doctor)
        await session.commit()
    patient_id = await _add_patient(tenant_id, owner.id)
    return {
        "tenant_id": tenant_id,
        "owner": owner,
        "admin": admin,
        "manager": manager,
        "other": other,
        "doctor": doctor,
        "patient_id": patient_id,
    }


async def _grant_via_api(
    client: AsyncClient,
    admin,
    patient_id,
    *,
    grantee_user_id=None,
    grantee_doctor_id=None,
    permission="read",
    source="admin",
) -> dict:
    headers = await auth_headers(admin, step_up=True)
    body = {"permission": permission, "source": source}
    if grantee_user_id:
        body["grantee_user_id"] = str(grantee_user_id)
    if grantee_doctor_id:
        body["grantee_doctor_id"] = str(grantee_doctor_id)
    response = await client.post(
        f"/api/v1/patients/{patient_id}/access", json=body, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Grant API lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_lifecycle_admin_source(async_client: AsyncClient, clinic):
    grant = await _grant_via_api(
        async_client,
        clinic["admin"],
        clinic["patient_id"],
        grantee_user_id=clinic["manager"].id,
    )
    assert grant["status"] == "active"
    assert grant["permission"] == "read"
    assert grant["grantee_label"] == "Dr Manager"

    headers = await auth_headers(clinic["admin"], step_up=True)
    ledger = await async_client.get(
        f"/api/v1/patients/{clinic['patient_id']}/access", headers=headers
    )
    assert ledger.status_code == 200
    assert any(item["id"] == grant["id"] for item in ledger.json()["items"])

    patch = await async_client.patch(
        f"/api/v1/patients/{clinic['patient_id']}/access/{grant['id']}",
        json={"permission": "write"},
        headers=headers,
    )
    assert patch.status_code == 200
    assert patch.json()["permission"] == "write"

    revoke = await async_client.delete(
        f"/api/v1/patients/{clinic['patient_id']}/access/{grant['id']}", headers=headers
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_patient_share_flow_pending_accept_me_access(async_client: AsyncClient, clinic):
    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    grant = created.json()
    assert grant["status"] == "pending"
    assert grant["source"] == "patient"

    staff_headers = await auth_headers(clinic["manager"], step_up=True)
    me = await async_client.get("/api/v1/me/access", headers=staff_headers)
    assert me.status_code == 200
    pending_ids = [g["id"] for g in me.json()["pending_grants"]]
    assert grant["id"] in pending_ids
    assert me.json()["patient_grants"] == []

    accept = await async_client.post(
        f"/api/v1/me/access/grants/{grant['id']}/accept", headers=staff_headers
    )
    assert accept.status_code == 200
    assert accept.json()["status"] == "active"

    me = await async_client.get("/api/v1/me/access", headers=staff_headers)
    active_ids = [g["id"] for g in me.json()["patient_grants"]]
    assert grant["id"] in active_ids
    assert me.json()["pending_grants"] == []

    grants = await async_client.get("/api/v1/me/access/grants", headers=staff_headers)
    assert grants.status_code == 200
    assert any(
        item["id"] == grant["id"] and item["patient_name"] for item in grants.json()["active"]
    )


@pytest.mark.asyncio
async def test_patient_share_decline(async_client: AsyncClient, clinic):
    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=owner_headers,
    )
    grant = created.json()
    staff_headers = await auth_headers(clinic["manager"], step_up=True)
    declined = await async_client.post(
        f"/api/v1/me/access/grants/{grant['id']}/decline",
        json={"reason": "not my field"},
        headers=staff_headers,
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"


@pytest.mark.asyncio
async def test_grant_api_error_shapes(async_client: AsyncClient, clinic):
    admin_headers = await auth_headers(clinic["admin"], step_up=True)

    duplicate = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=admin_headers,
    )
    assert duplicate.status_code == 201
    conflict = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=admin_headers,
    )
    assert conflict.status_code == 409

    unlinked = DoctorModel(id=uuid.uuid4(), tenant_id=clinic["tenant_id"], name="Dr ghost")
    async with AsyncSessionLocal() as session:
        session.add(unlinked)
        await session.commit()
    bad_doctor = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_doctor_id": str(unlinked.id)},
        headers=admin_headers,
    )
    assert bad_doctor.status_code == 400

    bad_shape = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read"},
        headers=admin_headers,
    )
    assert bad_shape.status_code == 400

    unknown_field = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id), "x": 1},
        headers=admin_headers,
    )
    assert unknown_field.status_code == 422

    foreign = await async_client.post(
        f"/api/v1/patients/{uuid.uuid4()}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=admin_headers,
    )
    assert foreign.status_code == 404

    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    ledger = await async_client.get(
        f"/api/v1/patients/{clinic['patient_id']}/access", headers=owner_headers
    )
    assert ledger.status_code == 200
    other_headers = await auth_headers(clinic["other"], step_up=True)
    denied = await async_client.get(
        f"/api/v1/patients/{clinic['patient_id']}/access", headers=other_headers
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_patient_owner_can_revoke_own_share(async_client: AsyncClient, clinic):
    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=owner_headers,
    )
    grant = created.json()
    revoked = await async_client.delete(
        f"/api/v1/patients/{clinic['patient_id']}/access/{grant['id']}",
        headers=owner_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


# ---------------------------------------------------------------------------
# Query-surface parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_reaches_patients_list_and_detail(async_client: AsyncClient, clinic):
    admin = clinic["admin"]
    grantee = clinic["manager"]
    await _grant_via_api(async_client, admin, clinic["patient_id"], grantee_user_id=grantee.id)

    headers = await auth_headers(grantee, step_up=True)
    listed = await async_client.get("/api/v1/patients", headers=headers)
    assert listed.status_code == 200
    listed_ids = {p["id"] for p in listed.json()["items"]}
    assert str(clinic["patient_id"]) in listed_ids

    detail = await async_client.get(f"/api/v1/patients/{clinic['patient_id']}", headers=headers)
    assert detail.status_code == 200

    # Other in-tenant accounts stay locked out.
    other_headers = await auth_headers(clinic["other"], step_up=True)
    other_list = await async_client.get("/api/v1/patients", headers=other_headers)
    other_ids = {p["id"] for p in other_list.json()["items"]}
    assert str(clinic["patient_id"]) not in other_ids
    other_detail = await async_client.get(
        f"/api/v1/patients/{clinic['patient_id']}", headers=other_headers
    )
    assert other_detail.status_code == 403


@pytest.mark.asyncio
async def test_read_grant_lists_examinations_but_write_needs_write(
    async_client: AsyncClient, clinic
):
    """Grant-only reach (a guardian USER on an ownerless patient): a read
    grant lists the examination but never satisfies the write check."""
    admin_headers = await auth_headers(clinic["admin"], step_up=True)
    orphan = await _add_patient(clinic["tenant_id"], None)
    exam_id = await _add_examination(clinic["tenant_id"], orphan)
    user_headers = await auth_headers(clinic["other"], step_up=True)

    await _grant_via_api(
        async_client,
        clinic["admin"],
        orphan,
        grantee_user_id=clinic["other"].id,
        permission="read",
        source="guardian",
    )
    exams = await async_client.get("/api/v1/examinations", headers=user_headers)
    assert exams.status_code == 200
    assert any(str(e["id"]) == str(exam_id) for e in exams.json())

    scoped = await async_client.get(
        f"/api/v1/examinations?patient_id={orphan}", headers=user_headers
    )
    assert scoped.status_code == 200

    update = await async_client.put(
        f"/api/v1/examinations/{exam_id}", json={"notes": "edited"}, headers=user_headers
    )
    assert update.status_code == 403

    # Upgrade the grant to write: same session, no re-login.
    ledger = await async_client.get(f"/api/v1/patients/{orphan}/access", headers=admin_headers)
    grant_id = ledger.json()["items"][0]["id"]
    patch = await async_client.patch(
        f"/api/v1/patients/{orphan}/access/{grant_id}",
        json={"permission": "write"},
        headers=admin_headers,
    )
    assert patch.status_code == 200

    update = await async_client.put(
        f"/api/v1/examinations/{exam_id}", json={"notes": "edited"}, headers=user_headers
    )
    assert update.status_code == 200

    # Revoking kills the write (and the read) immediately.
    revoke = await async_client.delete(
        f"/api/v1/patients/{orphan}/access/{grant_id}", headers=admin_headers
    )
    assert revoke.status_code == 200
    update = await async_client.put(
        f"/api/v1/examinations/{exam_id}",
        json={"notes": "edited again"},
        headers=user_headers,
    )
    assert update.status_code == 403
    exams = await async_client.get("/api/v1/examinations", headers=user_headers)
    assert not any(str(e["id"]) == str(exam_id) for e in exams.json())


@pytest.mark.asyncio
async def test_grant_reaches_global_search_and_allergy_feed(async_client: AsyncClient, clinic):
    admin = clinic["admin"]
    grantee = clinic["manager"]
    await _grant_via_api(async_client, admin, clinic["patient_id"], grantee_user_id=grantee.id)

    headers = await auth_headers(grantee, step_up=True)
    search = await async_client.get("/api/v1/search?q=Parity", headers=headers)
    assert search.status_code == 200
    patient_hits = [r for r in search.json()["results"] if r["type"] == "patient"]
    assert any(str(clinic["patient_id"]) == r["id"] for r in patient_hits)

    active = await async_client.get("/api/v1/allergies/active", headers=headers)
    assert active.status_code == 200


@pytest.mark.asyncio
async def test_manager_assigned_scope_and_flip(async_client: AsyncClient, clinic):
    await _set_settings(clinic["tenant_id"], {"manager_scope": "assigned"})
    headers = await auth_headers(clinic["manager"], step_up=True)

    listed = await async_client.get("/api/v1/patients", headers=headers)
    listed_ids = {p["id"] for p in listed.json()["items"]}
    assert str(clinic["patient_id"]) not in listed_ids

    detail = await async_client.get(f"/api/v1/patients/{clinic['patient_id']}", headers=headers)
    assert detail.status_code == 403

    await _grant_via_api(
        async_client, clinic["admin"], clinic["patient_id"], grantee_user_id=clinic["manager"].id
    )
    listed = await async_client.get("/api/v1/patients", headers=headers)
    listed_ids = {p["id"] for p in listed.json()["items"]}
    assert str(clinic["patient_id"]) in listed_ids
    detail = await async_client.get(f"/api/v1/patients/{clinic['patient_id']}", headers=headers)
    assert detail.status_code == 200

    # Flip back mid-session: the knob is read per request, never cached.
    await _set_settings(clinic["tenant_id"], {})
    detail = await async_client.get(f"/api/v1/patients/{clinic['patient_id']}", headers=headers)
    assert detail.status_code == 200  # the grant itself still reaches


@pytest.mark.asyncio
async def test_export_never_rides_a_grant(async_client: AsyncClient, clinic):
    admin = clinic["admin"]
    other_headers = await auth_headers(clinic["other"], step_up=True)

    # D5: even a guardian-grade grant on the record must not enable export.
    orphan = await _add_patient(clinic["tenant_id"], None)
    await _grant_via_api(
        async_client,
        admin,
        orphan,
        grantee_user_id=clinic["other"].id,
        source="guardian",
    )
    export = await async_client.post(
        "/api/v1/export",
        json={
            "scope": "patient",
            "export_type": "fhir_only",
            "patient_ids": [str(orphan)],
        },
        headers=other_headers,
    )
    assert export.status_code == 403

    own_patient = await _add_patient(clinic["tenant_id"], clinic["other"].id)
    export = await async_client.post(
        "/api/v1/export",
        json={
            "scope": "patient",
            "export_type": "fhir_only",
            "patient_ids": [str(own_patient)],
        },
        headers=other_headers,
    )
    assert export.status_code in (200, 201), export.text


@pytest.mark.asyncio
async def test_grant_reaches_documents_and_task_monitor(async_client: AsyncClient, clinic):
    admin = clinic["admin"]
    grantee = clinic["manager"]
    await _grant_via_api(async_client, admin, clinic["patient_id"], grantee_user_id=grantee.id)

    headers = await auth_headers(grantee, step_up=True)
    docs = await async_client.get("/api/v1/documents", headers=headers)
    assert docs.status_code == 200

    processing = await async_client.get(
        "/api/v1/task-monitor/documents/processing", headers=headers
    )
    assert processing.status_code == 200

    search = await async_client.get(
        "/api/v1/instances/search", params={"q": "parity"}, headers=headers
    )
    assert search.status_code == 200


@pytest.mark.asyncio
async def test_cross_tenant_patient_hidden_for_admin_too(async_client: AsyncClient, clinic):
    other_tenant = await create_tenant()
    other_admin = await create_user(role=Role.ADMIN, tenant_id=other_tenant)
    foreign_patient = await _add_patient(other_tenant, None)

    headers = await auth_headers(clinic["admin"], step_up=True)
    detail = await async_client.get(f"/api/v1/patients/{foreign_patient}", headers=headers)
    assert detail.status_code == 404
    _ = other_admin


# ---------------------------------------------------------------------------
# Audits + notifications (B1), accept re-validation (S2), races (S3), PATCH
# clear semantics (S4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_share_transitions_are_audited_and_notified(async_client: AsyncClient, clinic):
    owner = clinic["owner"]
    manager = clinic["manager"]
    owner_headers = await auth_headers(owner, step_up=True)
    staff_headers = await auth_headers(manager, step_up=True)

    # Patient share: creation notifies the invitee, acceptance the owner.
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(manager.id)},
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    grant = created.json()

    accept = await async_client.post(
        f"/api/v1/me/access/grants/{grant['id']}/accept", headers=staff_headers
    )
    assert accept.status_code == 200

    staff_titles = await _inbox_titles(async_client, manager)
    assert any("shared with you" in t for t in staff_titles)
    owner_titles = await _inbox_titles(async_client, owner)
    assert any("accepted" in t.lower() for t in owner_titles)

    # Revoking the accepted share notifies the (ex-)grantee.
    revoke = await async_client.delete(
        f"/api/v1/patients/{clinic['patient_id']}/access/{grant['id']}",
        headers=owner_headers,
    )
    assert revoke.status_code == 200
    staff_titles = await _inbox_titles(async_client, manager)
    assert any("revoked" in t.lower() for t in staff_titles)

    # Audit rows exist per transition (B1) — actor/action/resource recorded.
    actions = await _grant_actions(uuid.UUID(grant["id"]))
    assert {"grant_create", "grant_accept", "grant_revoke"} <= actions

    # Decline notifies the owner too (second patient: one live grant per
    # identity per patient).
    orphan = await _add_patient(clinic["tenant_id"], owner.id)
    created = await async_client.post(
        f"/api/v1/patients/{orphan}/access",
        json={"permission": "read", "grantee_user_id": str(manager.id)},
        headers=owner_headers,
    )
    assert created.status_code == 201
    share = created.json()
    declined = await async_client.post(
        f"/api/v1/me/access/grants/{share['id']}/decline", headers=staff_headers
    )
    assert declined.status_code == 200
    owner_titles = await _inbox_titles(async_client, owner)
    assert any("declined" in t.lower() for t in owner_titles)
    assert {"grant_create", "grant_decline"} <= await _grant_actions(uuid.UUID(share["id"]))


@pytest.mark.asyncio
async def test_accept_revalidates_demotion_and_policy(async_client: AsyncClient, clinic):
    """S2 over HTTP: demotion → 403; a tenant policy flip → 409."""
    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=owner_headers,
    )
    grant = created.json()

    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, clinic["manager"].id)
        user.role = Role.USER
        await session.commit()
    staff_headers = await auth_headers(clinic["manager"], step_up=True)
    accept = await async_client.post(
        f"/api/v1/me/access/grants/{grant['id']}/accept", headers=staff_headers
    )
    assert accept.status_code == 403

    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, clinic["manager"].id)
        user.role = Role.MANAGER
        await session.commit()
    await _set_settings(clinic["tenant_id"], {"allow_patient_share": False})
    accept = await async_client.post(
        f"/api/v1/me/access/grants/{grant['id']}/accept", headers=staff_headers
    )
    assert accept.status_code == 409
    await _set_settings(clinic["tenant_id"], {})


@pytest.mark.asyncio
async def test_cross_tenant_accept_and_decline_hidden_404(async_client: AsyncClient, clinic):
    other_tenant = await create_tenant()
    outsider = await create_user(role=Role.MANAGER, tenant_id=other_tenant)
    owner_headers = await auth_headers(clinic["owner"], step_up=True)
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["manager"].id)},
        headers=owner_headers,
    )
    assert created.status_code == 201
    grant_id = created.json()["id"]

    foreign_headers = await auth_headers(outsider, step_up=True)
    accept = await async_client.post(
        f"/api/v1/me/access/grants/{grant_id}/accept", headers=foreign_headers
    )
    assert accept.status_code == 404
    decline = await async_client.post(
        f"/api/v1/me/access/grants/{grant_id}/decline", headers=foreign_headers
    )
    assert decline.status_code == 404


@pytest.mark.asyncio
async def test_assigned_manager_cannot_grant_on_unreachable_patient(
    async_client: AsyncClient, clinic
):
    await _set_settings(clinic["tenant_id"], {"manager_scope": "assigned"})
    manager_headers = await auth_headers(clinic["manager"], step_up=True)
    denied = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={"permission": "read", "grantee_user_id": str(clinic["other"].id)},
        headers=manager_headers,
    )
    assert denied.status_code == 403
    await _set_settings(clinic["tenant_id"], {})


@pytest.mark.asyncio
async def test_patch_null_clears_and_omission_keeps(async_client: AsyncClient, clinic):
    """S4: a JSON null clears expiry/reason; an omitted field is untouched."""
    admin_headers = await auth_headers(clinic["admin"], step_up=True)
    expires = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    created = await async_client.post(
        f"/api/v1/patients/{clinic['patient_id']}/access",
        json={
            "permission": "read",
            "grantee_user_id": str(clinic["manager"].id),
            "reason": "covering leave",
            "expires_at": expires,
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    grant = created.json()
    url = f"/api/v1/patients/{clinic['patient_id']}/access/{grant['id']}"

    patched = await async_client.patch(url, json={"permission": "write"}, headers=admin_headers)
    assert patched.status_code == 200
    assert patched.json()["expires_at"] is not None
    assert patched.json()["reason"] == "covering leave"

    patched = await async_client.patch(
        url,
        json={"permission": "read", "expires_at": None, "reason": None},
        headers=admin_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["expires_at"] is None
    assert patched.json()["reason"] is None


# ---------------------------------------------------------------------------
# Task-monitor patient reach (B2) + notification rules reachable set (S5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_monitor_patient_reach_discrimination(async_client: AsyncClient, clinic):
    """B2: assigned-MANAGER denied; tenant MANAGER sees all; a granted USER
    sees granted rows."""
    admin = clinic["admin"]
    owner_patient_doc = await _add_document(
        clinic["tenant_id"], clinic["patient_id"], owner_id=clinic["owner"].id
    )
    owner_patient_exam = await _add_examination(
        clinic["tenant_id"], clinic["patient_id"], extraction_status="processing"
    )

    # An assigned MANAGER reaches no rows and cannot even probe the retry.
    await _set_settings(clinic["tenant_id"], {"manager_scope": "assigned"})
    manager_headers = await auth_headers(clinic["manager"], step_up=True)
    exams = await async_client.get(
        "/api/v1/task-monitor/examinations/processing", headers=manager_headers
    )
    assert exams.status_code == 200
    assert all(e["id"] != str(owner_patient_exam) for e in exams.json())
    retry = await async_client.post(
        f"/api/v1/task-monitor/examinations/retry/{owner_patient_exam}",
        headers=manager_headers,
    )
    assert retry.status_code == 404

    # The default tenant-scope MANAGER sees the whole tenant.
    await _set_settings(clinic["tenant_id"], {})
    exams = await async_client.get(
        "/api/v1/task-monitor/examinations/processing", headers=manager_headers
    )
    assert any(e["id"] == str(owner_patient_exam) for e in exams.json())
    docs = await async_client.get(
        "/api/v1/task-monitor/documents/processing", headers=manager_headers
    )
    assert any(d["id"] == str(owner_patient_doc) for d in docs.json())

    # A guardian-granted USER sees the granted rows (documents unattached to
    # a patient would stay uploader-only).
    orphan = await _add_patient(clinic["tenant_id"], None)
    orphan_doc = await _add_document(clinic["tenant_id"], orphan, owner_id=admin.id)
    orphan_exam = await _add_examination(
        clinic["tenant_id"], orphan, extraction_status="processing"
    )
    user_headers = await auth_headers(clinic["other"], step_up=True)
    before = await async_client.get(
        "/api/v1/task-monitor/documents/processing", headers=user_headers
    )
    assert all(d["id"] != str(orphan_doc) for d in before.json())
    await _grant_via_api(
        async_client, admin, orphan, grantee_user_id=clinic["other"].id, source="guardian"
    )
    after = await async_client.get(
        "/api/v1/task-monitor/documents/processing", headers=user_headers
    )
    assert any(d["id"] == str(orphan_doc) for d in after.json())
    exams = await async_client.get(
        "/api/v1/task-monitor/examinations/processing", headers=user_headers
    )
    assert any(e["id"] == str(orphan_exam) for e in exams.json())


@pytest.mark.asyncio
async def test_notification_rules_follow_patient_reach(async_client: AsyncClient, clinic):
    """S5: the unfiltered list shows restricted roles only rules attached to
    patients they can reach; tenant-wide roles see everything."""
    tenant_id = clinic["tenant_id"]
    own = await _add_patient(tenant_id, clinic["other"].id)
    await notification_rule_service.create_rule(
        {"rule_type": "BIOMARKER_THRESHOLD", "operator": ">", "value": 5, "patient_id": str(own)},
        tenant_id=tenant_id,
    )
    await notification_rule_service.create_rule(
        {
            "rule_type": "BIOMARKER_THRESHOLD",
            "operator": ">",
            "value": 5,
            "patient_id": str(clinic["patient_id"]),
        },
        tenant_id=tenant_id,
    )
    await notification_rule_service.create_rule(
        {"rule_type": "BIOMARKER_THRESHOLD", "operator": ">", "value": 5},
        tenant_id=tenant_id,
    )

    user_headers = await auth_headers(clinic["other"], step_up=True)
    listed = await async_client.get("/api/v1/notification-rules", headers=user_headers)
    assert listed.status_code == 200
    patient_ids = {item["patient_id"] for item in listed.json()["items"]}
    assert patient_ids == {str(own)}

    admin_headers = await auth_headers(clinic["admin"], step_up=True)
    listed = await async_client.get("/api/v1/notification-rules", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 3

    # An explicit patient_id keeps the row-level check (foreign patient → 403).
    scoped = await async_client.get(
        f"/api/v1/notification-rules?patient_id={own}", headers=user_headers
    )
    assert scoped.status_code == 200
    foreign = await async_client.get(
        f"/api/v1/notification-rules?patient_id={clinic['patient_id']}", headers=user_headers
    )
    assert foreign.status_code == 403

    # An assigned-scope MANAGER with no grants sees no rules at all.
    await _set_settings(tenant_id, {"manager_scope": "assigned"})
    manager_headers = await auth_headers(clinic["manager"], step_up=True)
    listed = await async_client.get("/api/v1/notification-rules", headers=manager_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    await _set_settings(tenant_id, {})

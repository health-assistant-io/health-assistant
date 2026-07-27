"""Tests for the in-app guided-setup checklist service + endpoint.

The service (`SetupChecklistService`) derives step state from live data, so
these tests run against the real migrated test DB — they create a tenant,
a user, and progressively complete data, asserting the right steps flip
to ``completed`` and the ``completion`` ratio advances.

Covers role steps for USER / ADMIN / SYSTEM_ADMIN and the patient entity
checklist (birth_date / address / telecom / emergency_contact / race /
ethnicity / preferred_language / insurance_provider / allergies /
current_medications / current_events).
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.enums import (
    Gender,
    Role,
)
from app.models.fhir.patient import Patient
from app.schemas.user import TokenData
from app.services.fhir_extensions import validate_patient_extensions
from app.services.setup_checklist_service import (
    SUPPORTED_ENTITIES,
    SetupChecklistService,
)


def _make_token(role: Role, user_id, tenant_id) -> TokenData:
    """Build a lightweight inner TokenData-equivalent object."""
    return TokenData(
        sub="x@x.local",
        user_id=user_id,
        tenant_id=tenant_id,
        role=role.value,
    )


async def _make_tenant_and_user(role: Role = Role.USER):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": tenant_id, "n": f"T-{tenant_id.hex[:6]}", "s": f"t-{tenant_id.hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, hashed_password, settings) "
                "VALUES (:id, :tid, :email, :role, 'x', '{}'::jsonb)"
            ),
            {"id": user_id, "tid": tenant_id, "email": f"u-{user_id.hex[:6]}@t.local", "role": role.value},
        )
        await session.commit()
    return tenant_id, user_id


async def _cleanup(*ids):
    """Best-effort delete by id per table."""
    async with AsyncSessionLocal() as session:
        for table, ident in ids:
            await session.execute(
                text(f"DELETE FROM {table} WHERE id = :id"), {"id": ident}
            )
        await session.commit()


# ---------- Role checklists: shape ----------


@pytest.mark.asyncio
async def test_user_role_has_expected_steps_and_blank_at_start():
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    token = _make_token(Role.USER, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    ids = [s.id for s in steps]
    assert "user.preferences_language" in ids
    assert "user.linked_self_patient" in ids
    # Nothing complete at start
    assert all(s.completed is False for s in steps)

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_admin_role_has_org_patient_doctor_steps():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    token = _make_token(Role.ADMIN, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    ids = {s.id for s in steps}
    assert {
        "tenant.first_org",
        "tenant.first_patient",
        "tenant.first_doctor",
        "tenant.ai_config",
        "tenant.member_invited",
    } <= ids

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_system_admin_role_has_system_steps():
    tenant_id, user_id = await _make_tenant_and_user(Role.SYSTEM_ADMIN)
    token = _make_token(Role.SYSTEM_ADMIN, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    ids = {s.id for s in steps}
    assert {
        "system.first_tenant",
        "system.catalog_seeded",
        "tenant.first_patient",
        "system.ai_config",
        "system.first_user",
        "system.integrations_review",
    } <= ids

    await _cleanup(("users", user_id), ("tenants", tenant_id))


# ---------- Role steps: completion flips ----------


@pytest.mark.asyncio
async def test_user_linked_self_patient_step_completes_when_patient_created():
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients "
                "(id, tenant_id, user_id, name, gender) "
                "VALUES (:id, :tid, :uid, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "uid": user_id, "name": '{"family": "X"}'},
        )
        await session.commit()

        token = _make_token(Role.USER, user_id, tenant_id)
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    by_id = {s.id: s.completed for s in steps}
    assert by_id["user.linked_self_patient"] is True

    await _cleanup(("fhir_patients", patient_id), ("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_tenant_first_patient_step_completes_for_admin():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (:id, :tid, :name, 'FEMALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "name": '{"family": "X"}'},
        )
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    assert {s.id: s.completed for s in steps}["tenant.first_patient"] is True

    await _cleanup(("fhir_patients", patient_id), ("users", user_id), ("tenants", tenant_id))


# ---------- get_checklist integration ----------


@pytest.mark.asyncio
async def test_get_checklist_role_only_when_no_entity():
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    async with AsyncSessionLocal() as session:
        token = _make_token(Role.USER, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token)
    assert resp.role == Role.USER.value
    assert resp.entity is None
    assert resp.entity_id is None
    assert resp.completion == 0.0
    assert len(resp.steps) >= 2

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_get_checklist_with_patient_entity_includes_entity_steps():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        patient = Patient(
            id=patient_id,
            tenant_id=tenant_id,
            name=[{"family": "Doe"}],
            gender=Gender.MALE,
        )
        session.add(patient)
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)
    by_id = {s.id: s.completed for s in resp.steps}
    # Demographics steps present
    assert "patient.birth_date" in by_id
    assert "patient.allergies" in by_id
    # Patient entity_id resolved
    assert resp.entity == "patient"
    assert resp.entity_id == patient_id
    # Mandatory steps out of the box: birth_date, address, telecom (no race/ethnicity optional)
    assert 0.0 <= resp.completion <= 1.0

    await _cleanup(("fhir_patients", patient_id), ("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_unsupported_entity_raises():
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    async with AsyncSessionLocal() as session:
        from app.core.errors import ValidationError

        token = _make_token(Role.USER, user_id, tenant_id)
        service = SetupChecklistService(session)
        with pytest.raises(ValidationError):
            await service.get_checklist(token, entity="not_real", entity_id=uuid.uuid4())

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_entity_without_entity_id_raises():
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    async with AsyncSessionLocal() as session:
        from app.core.errors import ValidationError

        token = _make_token(Role.USER, user_id, tenant_id)
        service = SetupChecklistService(session)
        with pytest.raises(ValidationError):
            await service.get_checklist(token, entity="patient", entity_id=None)

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_optional_steps_excluded_from_completion_ratio():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        # Fully complete mandatory demographics + optional steps
        cleaned = validate_patient_extensions(
            {
                "race": {"text": "Black"},
                "preferred_language": "el",
                "insurance_provider": "Acme",
            }
        )
        patient = Patient(
            id=patient_id,
            tenant_id=tenant_id,
            name=[{"family": "Doe"}],
            gender=Gender.MALE,
            birth_date=__import__("datetime").date(1980, 1, 1),
            address=[{"text": "1 Main"}],
            telecom=[{"system": "phone", "value": "555"}],
            emergency_contact={"name": "Jane"},
            extensions=cleaned,
        )
        session.add(patient)
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)

    by_id = {s.id: s.completed for s in resp.steps}
    # Mandatory patient steps are all complete
    assert by_id["patient.birth_date"] is True
    assert by_id["patient.address"] is True
    assert by_id["patient.telecom"] is True
    # Optional steps also complete
    assert by_id["patient.race_or_ethnicity"] is True
    assert by_id["patient.preferred_language"] is True
    assert by_id["patient.insurance_provider"] is True
    assert by_id["patient.emergency_contact"] is True
    # Any optional aggregation step (allergies/meds/events) stays false, but
    # because optional they do NOT pull completion below the mandatory ratio.
    # completion may be < 1 if any *role* mandatory step is incomplete (e.g.
    # tenant.first_org). Assert mandatory patient-only ratio reaches 1.
    patient_steps = [s for s in resp.steps if s.entity == "patient" and not s.optional]
    assert all(s.completed for s in patient_steps)
    # Optional steps count as completed in the optional pool too
    optional = [s for s in resp.steps if s.optional and s.entity == "patient"]
    optional_completed = [s for s in optional if s.completed]
    assert len(optional_completed) == 4  # race+language+insurance+emergency_contact

    await _cleanup(("fhir_patients", patient_id), ("users", user_id), ("tenants", tenant_id))


# ---------- Aggregation steps: allergies / medications / events ----------


@pytest.mark.asyncio
async def test_patient_allergies_step_completes_when_allergy_added():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    allergy_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (:id, :tid, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "name": '{"family": "Doe"}'},
        )
        await session.execute(
            text(
                "INSERT INTO fhir_allergy_intolerances "
                "(id, tenant_id, patient_id, code, clinical_status) "
                "VALUES (:id, :tid, :pid, :code, 'ACTIVE')"
            ),
            {"id": allergy_id, "tid": tenant_id, "pid": patient_id, "code": '{"text":"Peanut"}'},
        )
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)
    assert {s.id: s.completed for s in resp.steps}["patient.allergies"] is True

    await _cleanup(
        ("fhir_allergy_intolerances", allergy_id),
        ("fhir_patients", patient_id),
        ("users", user_id),
        ("tenants", tenant_id),
    )


@pytest.mark.asyncio
async def test_patient_current_medications_step_only_counts_active():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    med_active = uuid.uuid4()
    med_inactive = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (:id, :tid, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "name": '{"family": "Doe"}'},
        )
        for mid, status in ((med_active, "ACTIVE"), (med_inactive, "STOPPED")):
            await session.execute(
                text(
                    "INSERT INTO fhir_medications "
                    "(id, tenant_id, patient_id, code, status, intent) "
                    "VALUES (:id, :tid, :pid, :code, :status, 'statement')"
                ),
                {"id": mid, "tid": tenant_id, "pid": patient_id, "code": '{"text":"M"}', "status": status},
            )
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)
    assert {s.id: s.completed for s in resp.steps}["patient.current_medications"] is True

    await _cleanup(
        ("fhir_medications", med_active),
        ("fhir_medications", med_inactive),
        ("fhir_patients", patient_id),
        ("users", user_id),
        ("tenants", tenant_id),
    )


@pytest.mark.asyncio
async def test_patient_current_medications_step_incomplete_with_only_stopped():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    med_inactive = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (:id, :tid, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "name": '{"family": "Doe"}'},
        )
        await session.execute(
            text(
                "INSERT INTO fhir_medications "
                "(id, tenant_id, patient_id, code, status, intent) "
                "VALUES (:id, :tid, :pid, :code, 'STOPPED', 'statement')"
            ),
            {"id": med_inactive, "tid": tenant_id, "pid": patient_id, "code": '{"text":"M"}'},
        )
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)
    assert {s.id: s.completed for s in resp.steps}["patient.current_medications"] is False

    await _cleanup(
        ("fhir_medications", med_inactive),
        ("fhir_patients", patient_id),
        ("users", user_id),
        ("tenants", tenant_id),
    )


@pytest.mark.asyncio
async def test_patient_current_events_step_completes_when_event_added():

    from app.models.clinical_event import ClinicalEvent, ClinicalEventStatus

    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (:id, :tid, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "name": '{"family": "Doe"}'},
        )
        session.add(
            ClinicalEvent(
                id=event_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                title="Chronic Migraine",
                status=ClinicalEventStatus.ACTIVE,
            )
        )
        await session.commit()

        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        resp = await service.get_checklist(token, entity="patient", entity_id=patient_id)
    assert {s.id: s.completed for s in resp.steps}["patient.current_events"] is True

    await _cleanup(
        ("clinical_events", event_id),
        ("fhir_patients", patient_id),
        ("users", user_id),
        ("tenants", tenant_id),
    )


# ---------- Access control for the patient entity checklist ----------


@pytest.mark.asyncio
async def test_user_role_cannot_read_other_users_patient_checklist():
    tenant_id, admin_id = await _make_tenant_and_user(Role.ADMIN)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients (id, tenant_id, user_id, name, gender) "
                "VALUES (:id, :tid, :admin, :name, 'MALE')"
            ),
            {"id": patient_id, "tid": tenant_id, "admin": admin_id, "name": '{"family": "Doe"}'},
        )
        await session.commit()

        # Different user with USER role in same tenant — must NOT have access.
        another_user = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, role, hashed_password, settings) "
                "VALUES (:id, :tid, :email, 'USER', 'x', '{}'::jsonb)"
            ),
            {"id": another_user, "tid": tenant_id, "email": f"u-{another_user.hex[:6]}@t.local"},
        )
        await session.commit()

        token = _make_token(Role.USER, another_user, tenant_id)
        service = SetupChecklistService(session)
        with pytest.raises(Exception):  # AuthorizationError
            await service.get_entity_checklist(token, "patient", patient_id)

    await _cleanup(
        ("users", another_user),
        ("fhir_patients", patient_id),
        ("users", admin_id),
        ("tenants", tenant_id),
    )


# ---------- Endpoint smoke ----------


@pytest.mark.asyncio
async def test_setup_endpoint_returns_role_only_for_user(async_client):
    from app.main import app
    from app.core.security import get_current_user

    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    from unittest.mock import MagicMock

    token = MagicMock()
    token.user_id = user_id
    token.tenant_id = tenant_id
    token.role = Role.USER.value
    app.dependency_overrides[get_current_user] = lambda: token
    try:
        r = await async_client.get("/api/v1/setup/checklist")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] in ("USER", "user")
        assert body["entity"] is None
        assert all(s["entity"] is None for s in body["steps"])
        assert "completion" in body
    finally:
        app.dependency_overrides = {}
        await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_setup_endpoint_rejects_unsupported_entity(async_client):
    from app.main import app
    from app.core.security import get_current_user
    from unittest.mock import MagicMock

    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    token = MagicMock()
    token.user_id = user_id
    token.tenant_id = tenant_id
    token.role = Role.ADMIN.value
    app.dependency_overrides[get_current_user] = lambda: token
    try:
        r = await async_client.get(
            "/api/v1/setup/checklist",
            params={"entity": "not_real", "entity_id": str(uuid.uuid4())},
        )
        # ValidationError → 400 via the global handler
        assert r.status_code in (400, 422)
    finally:
        app.dependency_overrides = {}
        await _cleanup(("users", user_id), ("tenants", tenant_id))


def test_supported_entities_is_patient_only():
    assert SUPPORTED_ENTITIES == ("patient",)


# ---------- Extension catalog ----------


@pytest.mark.asyncio
async def test_extension_catalog_returns_four_patient_extensions():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        catalog = await service.get_extension_catalog()
    keys = {e.key for e in catalog.extensions}
    assert keys == {"race", "ethnicity", "preferred_language", "insurance_provider"}
    assert catalog.entity == "patient"
    by_key = {e.key: e for e in catalog.extensions}
    assert by_key["race"].value_type == "omb_category"
    assert by_key["ethnicity"].value_type == "omb_category"
    assert by_key["preferred_language"].value_type == "code"
    assert by_key["insurance_provider"].value_type == "string"

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_extension_catalog_omb_race_options_present():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        catalog = await service.get_extension_catalog()
    race = next(e for e in catalog.extensions if e.key == "race")
    ethnicity = next(e for e in catalog.extensions if e.key == "ethnicity")
    language = next(e for e in catalog.extensions if e.key == "preferred_language")
    insurance = next(e for e in catalog.extensions if e.key == "insurance_provider")
    # OMB race: 5 OMB minimum categories
    assert race.options is not None and len(race.options) == 5
    assert {o.code for o in race.options} == {
        "1002-5", "2028-9", "2054-5", "2076-8", "2106-3"
    }
    # Ethnicity: 2 OMB categories
    assert ethnicity.options is not None and len(ethnicity.options) == 2
    # Languages: non-empty picklist
    assert language.options is not None and len(language.options) > 0
    # Free-text insurance has no options
    assert insurance.options is None

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_extension_catalog_rejects_unsupported_entity():
    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    from app.core.errors import ValidationError

    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        with pytest.raises(ValidationError):
            await service.get_extension_catalog(entity="doctor")

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_extension_catalog_endpoint_returns_catalog(async_client):
    from app.main import app
    from app.core.security import get_current_user
    from unittest.mock import MagicMock

    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    token = MagicMock()
    token.user_id = user_id
    token.tenant_id = tenant_id
    token.role = Role.ADMIN.value
    app.dependency_overrides[get_current_user] = lambda: token
    try:
        r = await async_client.get("/api/v1/setup/extension-catalog")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["entity"] == "patient"
        keys = {e["key"] for e in body["extensions"]}
        assert "race" in keys and "insurance_provider" in keys
    finally:
        app.dependency_overrides = {}
        await _cleanup(("users", user_id), ("tenants", tenant_id))


# ---------- payload_hint route correctness (regression guard) ----------

# Every redirect / external_config role-step route must match a real
# frontend route in App.tsx. A wrong route falls through to the catch-all
# `*` → <Dashboard/> which shows "no patient selected" — the exact bug this
# test guards against. Update BOTH sides together if a route moves.
EXPECTED_ROLE_ROUTES = {
    "user.preferences_language": "/settings/preferences",
    "user.linked_self_patient": "/patients?new=patient",
    "tenant.first_org": "/organizations?new",
    "tenant.first_patient": "/patients?new=patient",
    "tenant.first_doctor": "/doctors?new=doctor",
    "tenant.member_invited": "/admin/tenant/users",
    "system.first_tenant": "/admin/system/tenants",
    "system.first_user": "/admin/system/users",
    "system.integrations_review": "/admin/system/integrations",
}


@pytest.mark.asyncio
async def test_redirect_and_external_config_steps_carry_correct_routes():
    """Every redirect/external_config step's payload_hint.route must point
    at a real frontend route. The ``*.ai_config`` steps are ``external_config``
    with ``sub_steps`` (guided redirect) so they're checked separately."""
    checked = {}
    for role in (Role.USER, Role.ADMIN, Role.SYSTEM_ADMIN):
        tenant_id, user_id = await _make_tenant_and_user(role)
        token = _make_token(role, user_id, tenant_id)
        async with AsyncSessionLocal() as session:
            service = SetupChecklistService(session)
            steps = await service.get_role_checklist(token)
        for s in steps:
            if s.kind in ("redirect", "external_config") and s.id in EXPECTED_ROLE_ROUTES:
                checked[s.id] = s.payload_hint
                assert s.payload_hint is not None, f"{s.id} missing payload_hint"
                assert s.payload_hint.get("route") == EXPECTED_ROLE_ROUTES[s.id], (
                    f"{s.id} route mismatch: got {s.payload_hint.get('route')!r}, "
                    f"expected {EXPECTED_ROLE_ROUTES[s.id]!r}"
                )
        await _cleanup(("users", user_id), ("tenants", tenant_id))

    missing = set(EXPECTED_ROLE_ROUTES) - set(checked)
    assert not missing, f"these expected steps were never returned: {missing}"


@pytest.mark.asyncio
async def test_ai_config_step_has_sub_steps_payload():
    """The AI config step carries per-sub-step completion + route in
    payload_hint so the frontend guided checklist can deep-link to the
    right AI config tab."""
    # Other tests in the suite (test_ai_config_simple, test_api_key_encryption)
    # create SYSTEM-scope AI providers/models and don't clean up. The
    # _tenant_ai_config evaluator falls back to SYSTEM scope, so leaked rows
    # would flip ``done=True`` here. Wipe the AI tables to make this test
    # independent of execution order.
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM ai_task_assignments"))
        await session.execute(text("DELETE FROM ai_models"))
        await session.execute(text("DELETE FROM ai_providers"))
        await session.commit()

    tenant_id, user_id = await _make_tenant_and_user(Role.ADMIN)
    async with AsyncSessionLocal() as session:
        token = _make_token(Role.ADMIN, user_id, tenant_id)
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    ai_step = next((s for s in steps if s.id == "tenant.ai_config"), None)
    assert ai_step is not None
    assert ai_step.kind == "external_config"
    assert ai_step.payload_hint is not None
    sub = ai_step.payload_hint.get("sub_steps")
    assert isinstance(sub, list) and len(sub) == 3
    ids = [s["id"] for s in sub]
    assert ids == ["provider", "model", "assignment"]
    # With no AI config, all sub-steps should be False.
    assert all(s["done"] is False for s in sub)
    # Each carries a route to the right AI config tab.
    assert all("route" in s and "tab=" in s["route"] for s in sub)

    await _cleanup(("users", user_id), ("tenants", tenant_id))


# ---------- system.integrations_review step ----------


@pytest.mark.asyncio
async def test_system_integrations_review_step_default_state():
    """Integrations are enabled by default, so the review step is incomplete
    until the admin has toggled at least one SystemIntegration row, and it is
    optional so it never counts against the completion ratio."""
    tenant_id, user_id = await _make_tenant_and_user(Role.SYSTEM_ADMIN)
    token = _make_token(Role.SYSTEM_ADMIN, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    step = next(s for s in steps if s.id == "system.integrations_review")
    assert step.kind == "redirect"
    assert step.optional is True
    assert step.completed is False
    assert step.payload_hint == {"route": "/admin/system/integrations"}

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_system_integrations_review_completes_when_admin_acts():
    """Once any SystemIntegration row exists, the step flips to complete."""
    tenant_id, user_id = await _make_tenant_and_user(Role.SYSTEM_ADMIN)
    token = _make_token(Role.SYSTEM_ADMIN, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO system_integrations (domain, is_enabled) "
                "VALUES ('dev_dummy', false)"
            )
        )
        await session.commit()
        service = SetupChecklistService(session)
        steps = await service.get_role_checklist(token)
    step = next(s for s in steps if s.id == "system.integrations_review")
    assert step.completed is True

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM system_integrations WHERE domain = 'dev_dummy'")
        )
        await session.commit()
    await _cleanup(("users", user_id), ("tenants", tenant_id))


# ---------- manual-completion override ----------


@pytest.mark.asyncio
async def test_manual_complete_marks_step_done_and_persists():
    """set_manual_complete flips the step's effective completed state and
    persists the override in user.settings so the next read agrees."""
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    token = _make_token(Role.USER, user_id, tenant_id)

    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        before = await service.get_checklist(token)
        lang_step = next(s for s in before.steps if s.id == "user.preferences_language")
        assert lang_step.completed is False
        assert lang_step.manually_completed is False

        updated = await service.set_manual_complete(
            token, "user.preferences_language", True
        )
        assert updated.completed is True
        assert updated.manually_completed is True

        after = await service.get_checklist(token)
        after_step = next(s for s in after.steps if s.id == "user.preferences_language")
        assert after_step.completed is True
        assert after_step.manually_completed is True

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_manual_complete_clears_override():
    """Toggling back to False removes the override; the step reverts to its
    evaluator-derived state."""
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    token = _make_token(Role.USER, user_id, tenant_id)

    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        await service.set_manual_complete(token, "user.preferences_language", True)
        await service.set_manual_complete(token, "user.preferences_language", False)
        after = await service.get_checklist(token)
        step = next(s for s in after.steps if s.id == "user.preferences_language")
        assert step.completed is False
        assert step.manually_completed is False

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_manual_complete_does_not_flag_when_evaluator_already_complete():
    """When the evaluator already says complete, the manually_completed flag
    must stay False so the UI doesn't show a spurious 'undo' affordance."""
    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO fhir_patients "
                "(id, tenant_id, user_id, name, gender) "
                "VALUES (:id, :tid, :uid, :name, 'MALE')"
            ),
            {
                "id": patient_id,
                "tid": tenant_id,
                "uid": user_id,
                "name": '{"family": "Test"}',
            },
        )
        await session.commit()
    token = _make_token(Role.USER, user_id, tenant_id)

    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        # Step is genuinely complete (patient linked). Set a manual override
        # anyway — it should not flip manually_completed to True.
        await service.set_manual_complete(
            token, "user.linked_self_patient", True
        )
        after = await service.get_checklist(token)
        step = next(s for s in after.steps if s.id == "user.linked_self_patient")
        assert step.completed is True  # evaluator-derived
        assert step.manually_completed is False

    await _cleanup(
        ("fhir_patients", patient_id), ("users", user_id), ("tenants", tenant_id)
    )


@pytest.mark.asyncio
async def test_manual_complete_rejects_unknown_step():
    """The step id must belong to the caller's checklist scope — arbitrary
    writes for unrelated step ids are rejected."""
    from app.core.errors import ValidationError

    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    token = _make_token(Role.USER, user_id, tenant_id)
    async with AsyncSessionLocal() as session:
        service = SetupChecklistService(session)
        with pytest.raises(ValidationError):
            await service.set_manual_complete(
                token, "tenant.first_org", True  # not a USER-role step
            )

    await _cleanup(("users", user_id), ("tenants", tenant_id))


@pytest.mark.asyncio
async def test_manual_complete_endpoint_roundtrip(async_client):
    """The POST endpoint persists the override and returns the updated step."""
    from app.main import app
    from app.core.security import get_current_user
    from unittest.mock import MagicMock

    tenant_id, user_id = await _make_tenant_and_user(Role.USER)
    token = MagicMock()
    token.user_id = user_id
    token.tenant_id = tenant_id
    token.role = Role.USER.value
    app.dependency_overrides[get_current_user] = lambda: token
    try:
        r = await async_client.post(
            "/api/v1/setup/checklist/manual-complete",
            json={"step_id": "user.preferences_language", "completed": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["completed"] is True
        assert body["manually_completed"] is True

        # A fresh GET reflects the override.
        r2 = await async_client.get("/api/v1/setup/checklist")
        step = next(
            s for s in r2.json()["steps"] if s["id"] == "user.preferences_language"
        )
        assert step["completed"] is True
        assert step["manually_completed"] is True
    finally:
        app.dependency_overrides = {}
        await _cleanup(("users", user_id), ("tenants", tenant_id))
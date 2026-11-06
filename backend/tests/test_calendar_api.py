"""Calendar API: catalog, binding CRUD, custody over the wire, authz.

Pins:

* ``GET /calendar/providers`` renders the catalog from data (no secrets,
  no client ids, no hand-written provider list);
* an unknown provider / bad auth kind is a 422;
* ``redaction='full'`` is consent-gated (422 without the consent flag);
* a user touches only their own bindings — another tenant's user gets 404
  on PATCH/DELETE/sync and sees nothing in the list;
* custody: no credential material in any response (the secret set through
  the API never comes back), and the row is clean too;
* ``oauth2`` bindings refuse a pasted secret (400 — use the flow);
* sync on an unbound binding is a typed ``needs-secret`` result, never a
  crash and never a network call.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.calendar_binding import CalendarBinding, CalendarBindingCredential
from app.models.enums import Role
from app.models.fhir.patient import Patient
from tests._auth_helpers import auth_headers, create_user


async def _patient_for(tenant_id) -> uuid.UUID:
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Api"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
    return patient_id


@pytest.fixture
def data_key(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "HA_DATA_KEY", key)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    return key


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def owner_env():
    """(user, headers, patient_id) for the binding's owner."""
    user = await create_user(role=Role.USER, full_name="Cal Owner")
    patient_id = await _patient_for(user.tenant_id)
    return user, await auth_headers(user), patient_id


@pytest_asyncio.fixture
async def stranger_env():
    """(user, headers) for a user in a different tenant."""
    user = await create_user(role=Role.USER, full_name="Cal Stranger")
    return user, await auth_headers(user)


async def _create_binding(client, headers, patient_id, **payload_overrides) -> dict:
    payload = {
        "provider": "google",
        "patient_id": str(patient_id),
        "direction": "push",
        "redaction": "neutral",
    }
    payload.update(payload_overrides)
    response = await client.post("/api/v1/calendar/bindings", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_providers_render_the_catalog_without_secrets(client, owner_env):
    _, headers, _patient_id = owner_env
    response = await client.get("/api/v1/calendar/providers", headers=headers)
    assert response.status_code == 200
    body = response.json()
    provider_ids = [row["id"] for row in body["providers"]]
    assert "google" in provider_ids and "nextcloud" in provider_ids
    # consent data is verbatim scopes; secrets/client ids never appear
    text = response.text
    assert "client_secret" not in text and "clientSecret" not in text
    assert body["redactionDefault"] == "neutral"
    google = next(row for row in body["providers"] if row["id"] == "google")
    assert google["scopePresets"]["read"], "catalog scopes must be verbatim data"


@pytest.mark.asyncio
async def test_unknown_provider_is_422(client, owner_env):
    _, headers, patient_id = owner_env
    response = await client.post(
        "/api/v1/calendar/bindings",
        json={"provider": "aol-calendar", "patient_id": str(patient_id)},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_provider_without_calendar_service_is_422(client, owner_env):
    _, headers, patient_id = owner_env
    response = await client.post(
        "/api/v1/calendar/bindings",
        json={"provider": "dropbox", "patient_id": str(patient_id)},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_unknown_auth_kind(client, owner_env):
    _, headers, patient_id = owner_env
    response = await client.post(
        "/api/v1/calendar/bindings",
        json={"provider": "google", "auth_kind": "app-password", "patient_id": str(patient_id)},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_returns_the_pointer_not_a_secret(client, owner_env):
    _, headers, patient_id = owner_env
    binding = await _create_binding(client, headers, patient_id)
    assert binding["secret_ref"].startswith(f"calendar:{binding['id']}:oauth")
    assert binding["status"] == "unbound"
    assert binding["redaction"] == "neutral"
    for forbidden in ("access_token", "refresh_token", "password", "client_secret"):
        assert forbidden not in response_text(binding)


def response_text(obj) -> str:
    import json

    return json.dumps(obj, default=str)


@pytest.mark.asyncio
async def test_full_redaction_is_consent_gated(client, owner_env):
    _, headers, patient_id = owner_env
    denied = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "redaction": "full",
        },
        headers=headers,
    )
    assert denied.status_code == 422
    allowed = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "redaction": "full",
            "consent_full_details": True,
        },
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["redaction"] == "full"


@pytest.mark.asyncio
async def test_patch_validates_direction_and_redaction(client, owner_env):
    _, headers, patient_id = owner_env
    binding = await _create_binding(client, headers, patient_id)
    bad_direction = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"direction": "sideways"},
        headers=headers,
    )
    assert bad_direction.status_code == 422
    bad_redaction = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"redaction": "full"},
        headers=headers,
    )
    assert bad_redaction.status_code == 422
    ok = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"label": "Personal", "direction": "two-way"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["label"] == "Personal"
    assert ok.json()["direction"] == "two-way"


@pytest.mark.asyncio
async def test_cross_tenant_access_is_denied(client, owner_env, stranger_env):
    _, owner_headers, patient_id = owner_env
    _stranger_user, stranger_headers = stranger_env
    binding = await _create_binding(client, owner_headers, patient_id)

    listed = await client.get("/api/v1/calendar/bindings", headers=stranger_headers)
    assert listed.status_code == 200
    assert all(row["id"] != binding["id"] for row in listed.json())

    for method, path, body in (
        ("patch", f"/api/v1/calendar/bindings/{binding['id']}", {}),
        ("post", f"/api/v1/calendar/bindings/{binding['id']}/sync", None),
        ("post", f"/api/v1/calendar/bindings/{binding['id']}/test", None),
        (
            "post",
            f"/api/v1/calendar/bindings/{binding['id']}/secret",
            {"value": {"password": "x"}},
        ),
    ):
        response = await getattr(client, method)(path, headers=stranger_headers, json=body)
        assert response.status_code == 404, f"{method} {path} must be 404 across tenants"
    response = await client.delete(
        f"/api/v1/calendar/bindings/{binding['id']}", headers=stranger_headers
    )
    assert response.status_code == 404, "delete must be 404 across tenants"


@pytest.mark.asyncio
async def test_secret_set_is_sealed_and_never_echoed(client, owner_env, data_key):
    _, headers, patient_id = owner_env
    marker = "super-secret-app-password-value"
    binding = await _create_binding(
        client, headers, patient_id, provider="nextcloud", auth_kind="app-password"
    )
    response = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/secret",
        json={"value": {"password": marker, "username": "ilia"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert marker not in response.text
    assert response.json()["status"] == "ok"

    # the wire (list) is clean too
    listed = await client.get("/api/v1/calendar/bindings", headers=headers)
    assert marker not in listed.text
    # and the DB rows carry no plaintext outside the sealed blob
    async with AsyncSessionLocal() as db:
        cred = (
            await db.execute(
                select(CalendarBindingCredential).where(
                    CalendarBindingCredential.binding_id == uuid.UUID(binding["id"])
                )
            )
        ).scalar_one()
        assert marker not in cred.sealed_blob
        assert "_encrypted" in cred.sealed_blob
        binding_row = (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == uuid.UUID(binding["id"]))
            )
        ).scalar_one()
        assert marker not in repr(binding_row.__dict__)


@pytest.mark.asyncio
async def test_oauth2_binding_refuses_a_pasted_secret(client, owner_env, data_key):
    _, headers, patient_id = owner_env
    binding = await _create_binding(client, headers, patient_id, provider="google")
    response = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/secret",
        json={"value": {"token": "pasted"}},
        headers=headers,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_options_cannot_smuggle_credentials(client, owner_env):
    _, headers, patient_id = owner_env
    response = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "options": {"password": "sneaky"},
        },
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sync_without_secret_is_a_typed_needs_secret(client, owner_env):
    _, headers, patient_id = owner_env
    binding = await _create_binding(client, headers, patient_id)
    response = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/sync", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == "needs-secret"


@pytest.mark.asyncio
async def test_delete_removes_slots_and_links_but_not_records(client, owner_env, data_key):
    from datetime import UTC, datetime, timedelta

    from app.models.examination_model import ExaminationModel

    _, headers, patient_id = owner_env
    binding = await _create_binding(
        client, headers, patient_id, provider="nextcloud", auth_kind="app-password"
    )
    # a secret + a synced link
    await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/secret",
        json={"value": {"password": "p", "username": "u"}},
        headers=headers,
    )
    async with AsyncSessionLocal() as db:
        exam = ExaminationModel(
            tenant_id=uuid.UUID(binding["tenant_id"]),
            patient_id=uuid.UUID(binding["patient_id"]),
            examination_date=(datetime.now(UTC) + timedelta(days=5)).date(),
        )
        db.add(exam)
        await db.commit()
    await client.post(f"/api/v1/calendar/bindings/{binding['id']}/sync", headers=headers)

    deleted = await client.delete(
        f"/api/v1/calendar/bindings/{binding['id']}", headers=headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["secrets_deleted"] >= 1

    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == uuid.UUID(binding["id"]))
            )
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(CalendarBindingCredential).where(
                    CalendarBindingCredential.binding_id == uuid.UUID(binding["id"])
                )
            )
        ).scalars().all() == []
        surviving = (
            await db.execute(
                select(ExaminationModel).where(ExaminationModel.id == exam.id)
            )
        ).scalar_one()
        assert surviving.id == exam.id  # the clinical record survives unbind


@pytest.mark.asyncio
async def test_oauth_start_without_client_config_is_typed_needs_config(
    client, owner_env, monkeypatch
):
    import os

    _, headers, patient_id = owner_env
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", "")
    os.environ.pop("CALENDAR_OAUTH_CLIENT_IDS", None)
    binding = await _create_binding(client, headers, patient_id, provider="google")
    response = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/oauth/start", headers=headers
    )
    assert response.status_code == 400
    assert "needs-config" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected(client):
    response = await client.get("/api/v1/calendar/providers")
    assert response.status_code == 401

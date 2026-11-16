"""Scoped integration & extension principals (ADR 0054 D8, Phase 13).

Covers the replacement of owner-role impersonation:

- an integration resolves its own principal, provisioned from the extension's
  declared capabilities — never the owner's whole role;
- a bearer authenticates domain routes with the principal's permission subset
  and patient scope, and an empty intersection refuses;
- creation refuses permissions outside the owner or the manifest ceiling;
- rotation revokes the predecessor; revocation is honored mid-sync;
- an owner demotion narrows an outstanding principal on the next request;
- cross-tenant ids are hidden; scoped credentials cannot manage principals;
- use is audited with both the principal and the owner, and never plants the
  system context.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import tenant_context
from app.core.authz.roles import permissions_for
from app.core.database import AsyncSessionLocal
from app.core.errors import AuthorizationError, ValidationError
from app.models.audit_model import AuditEvent
from app.models.enums import IntegrationStatus, Role
from app.models.fhir.patient import Patient
from app.models.integration_principal import (
    IntegrationPrincipal,
    IntegrationPrincipalStatus,
)
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services.integration import integration_principals
from app.services.integration.integration_actor import resolve_integration_actor
from tests._auth_helpers import create_tenant, create_user, make_user_headers


def _headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _patient(tenant_id, user_id, *, name="Record") -> uuid.UUID:
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name={"family": name, "given": ["Own"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    return patient_id


async def _integration(tenant_id, user_id, patient_id, provider="dev_dummy") -> UserIntegration:
    row = UserIntegration(
        tenant_id=tenant_id,
        user_id=user_id,
        patient_id=patient_id,
        provider=provider,
        instance_name="pytest",
        status=IntegrationStatus.ACTIVE,
        user_config={},
    )
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def _create_principal(user: UserModel, scopes: list[str], **kwargs):
    async with AsyncSessionLocal() as db:
        row, secret = await integration_principals.create_principal(
            db,
            tenant_id=user.tenant_id,
            owner=user,
            name=kwargs.pop("name", "pytest principal"),
            scopes=scopes,
            **kwargs,
        )
        await db.commit()
        await db.refresh(row)
        return row, secret


async def _audit_actions(action: str) -> list[AuditEvent]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == action)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_integration_actor_never_impersonates_owner_role():
    """The provisioned principal carries a declared subset, not the role."""
    tenant_id = await create_tenant()
    owner, _headers_session = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    integration = await _integration(tenant_id, owner.id, patient_id)

    async with AsyncSessionLocal() as db:
        actor = await resolve_integration_actor(db, integration)
        await db.commit()

    assert actor.integration_principal_id is not None
    assert actor.user_id == owner.id
    scopes = set(actor.integration_permissions or [])
    assert scopes
    assert scopes < {p.value for p in permissions_for(Role.USER)}
    assert "patient:write" not in scopes, "the role's full reach must not leak"
    assert "tenant:read" not in scopes
    assert actor.integration_patient_scope == "granted"
    assert actor.integration_patient_ids == [str(patient_id)]

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(IntegrationPrincipal).where(
                    IntegrationPrincipal.integration_id == integration.id
                )
            )
        ).scalar_one()
        assert row.status == IntegrationPrincipalStatus.ACTIVE.value
        assert row.secret_hash is None, "internal principal carries no bearer secret"

    audit = await _audit_actions("integration_principal.provisioned")
    assert audit and str(audit[-1].user_id) == str(owner.id)


@pytest.mark.asyncio
async def test_bearer_is_limited_to_its_scopes_and_patient(async_client: AsyncClient):
    tenant_id = await create_tenant()
    owner, _headers_session = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    own_id = await _patient(tenant_id, owner.id, name="Mine")
    other_owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    other_id = await _patient(tenant_id, other_owner.id, name="Not Mine")

    _row, secret = await _create_principal(
        owner,
        ["patient:read"],
        patient_scope="granted",
        patient_ids=[own_id],
    )

    listing = await async_client.get("/api/v1/patients", headers=_headers(secret))
    assert listing.status_code == 200, listing.text
    assert {item["id"] for item in listing.json()["items"]} == {str(own_id)}

    allowed = await async_client.get(f"/api/v1/patients/{own_id}", headers=_headers(secret))
    assert allowed.status_code == 200

    denied_patient = await async_client.get(
        f"/api/v1/patients/{other_id}", headers=_headers(secret)
    )
    assert denied_patient.status_code == 403

    denied_scope = await async_client.get("/api/v1/documents", headers=_headers(secret))
    assert denied_scope.status_code == 403
    assert denied_scope.headers.get("X-Authz-Denied") == "1"

    denials = await _audit_actions("authz_denied")
    assert denials
    assert any(row.new_value.get("principal_id") == str(_row.id) for row in denials), (
        "a gate denial must attribute the principal, not only the owner"
    )


@pytest.mark.asyncio
async def test_creation_refuses_permissions_outside_owner_or_manifest(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _owner, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)

    beyond_owner = await async_client.post(
        "/api/v1/integration-principals",
        json={
            "name": "escalation",
            "kind": "service",
            "scopes": ["tenant:manage"],
            "patient_scope": "none",
        },
        headers=headers,
    )
    assert beyond_owner.status_code == 400, beyond_owner.text

    admin, admin_headers = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, admin.id)
    integration = await _integration(tenant_id, admin.id, patient_id)

    beyond_manifest = await async_client.post(
        "/api/v1/integration-principals",
        json={
            "name": "over-declared",
            "kind": "integration",
            "integration_id": str(integration.id),
            "scopes": ["tenant:manage"],
            "patient_scope": "granted",
            "patient_ids": [str(patient_id)],
        },
        headers=admin_headers,
    )
    assert beyond_manifest.status_code == 400, beyond_manifest.text
    assert "declared" in beyond_manifest.text


@pytest.mark.asyncio
async def test_rotation_revokes_the_predecessor_and_revocation_is_final(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    owner, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    row, secret = await _create_principal(
        owner, ["patient:read"], patient_scope="granted", patient_ids=[patient_id]
    )

    assert (await async_client.get("/api/v1/patients", headers=_headers(secret))).status_code == 200

    rotation = await async_client.post(
        f"/api/v1/integration-principals/{row.id}/rotate", headers=headers
    )
    assert rotation.status_code == 200, rotation.text
    replacement_secret = rotation.json()["secret"]
    assert replacement_secret != secret
    assert rotation.json()["integration_principal"]["rotated_from"] == str(row.id)

    assert (await async_client.get("/api/v1/patients", headers=_headers(secret))).status_code == 401
    assert (
        await async_client.get("/api/v1/patients", headers=_headers(replacement_secret))
    ).status_code == 200

    replacement_id = rotation.json()["integration_principal"]["id"]
    revoked = await async_client.delete(
        f"/api/v1/integration-principals/{replacement_id}", headers=headers
    )
    assert revoked.status_code == 204
    assert (
        await async_client.get("/api/v1/patients", headers=_headers(replacement_secret))
    ).status_code == 401


@pytest.mark.asyncio
async def test_revocation_mid_sync_is_honored():
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    integration = await _integration(tenant_id, owner.id, patient_id)

    async with AsyncSessionLocal() as db:
        await resolve_integration_actor(db, integration)
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(IntegrationPrincipal).where(
                    IntegrationPrincipal.integration_id == integration.id
                )
            )
        ).scalar_one()
        await integration_principals.revoke_principal(
            db,
            tenant_id=tenant_id,
            principal_id=row.id,
            actor_id=owner.id,
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await resolve_integration_actor(db, integration)


@pytest.mark.asyncio
async def test_owner_demotion_narrows_an_outstanding_principal(async_client: AsyncClient):
    tenant_id = await create_tenant()
    owner, _headers_admin = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    _row, secret = await _create_principal(owner, ["tenant:read"], patient_scope="all")

    before = await async_client.get("/api/v1/notifications/admin", headers=_headers(secret))
    assert before.status_code == 200, before.text

    async with AsyncSessionLocal() as db:
        user = await db.get(UserModel, owner.id)
        user.role = Role.USER
        db.add(user)
        await db.commit()

    after = await async_client.get("/api/v1/notifications/admin", headers=_headers(secret))
    assert after.status_code == 401, "a demoted owner's principal must stop resolving"


@pytest.mark.asyncio
async def test_cross_tenant_patient_is_hidden(async_client: AsyncClient):
    tenant_a = await create_tenant()
    other_owner = await create_user(role=Role.USER, tenant_id=tenant_a)
    patient_a = await _patient(tenant_a, other_owner.id, name="Tenant A")

    tenant_b = await create_tenant()
    owner_b = await create_user(role=Role.USER, tenant_id=tenant_b)
    patient_b = await _patient(tenant_b, owner_b.id, name="Tenant B")
    _row, secret = await _create_principal(
        owner_b, ["patient:read"], patient_scope="granted", patient_ids=[patient_b]
    )

    response = await async_client.get(f"/api/v1/patients/{patient_a}", headers=_headers(secret))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_use_is_audited_as_principal_and_owner(async_client: AsyncClient):
    tenant_id = await create_tenant()
    owner, _headers_session = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    row, secret = await _create_principal(
        owner, ["patient:read"], patient_scope="granted", patient_ids=[patient_id]
    )

    response = await async_client.get("/api/v1/patients", headers=_headers(secret))
    assert response.status_code == 200

    audit = await _audit_actions("integration_principal.used")
    assert audit
    latest = audit[-1]
    assert str(latest.user_id) == str(owner.id)
    assert latest.new_value["principal_id"] == str(row.id)
    assert latest.new_value["owner_user_id"] == str(owner.id)
    assert secret not in str(latest.new_value)
    assert row.secret_hash not in str(latest.new_value)


@pytest.mark.asyncio
async def test_scoped_credential_never_sets_system_context_and_cannot_manage(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    owner, _headers_session = await make_user_headers(role=Role.SYSTEM_ADMIN, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    integration = await _integration(tenant_id, owner.id, patient_id)

    async with AsyncSessionLocal() as db:
        row, secret = await integration_principals.create_principal(
            db,
            tenant_id=tenant_id,
            owner=owner,
            name="sys-owned",
            integration_id=integration.id,
            scopes=["patient:read"],
            patient_scope="granted",
            patient_ids=[patient_id],
        )
        await db.commit()
        await db.refresh(row)

    try:
        principal = await integration_principals.resolve_principal(secret)
        assert principal is not None
        assert tenant_context.system_enabled() is False
    finally:
        tenant_context.clear()

    manage = await async_client.post(
        "/api/v1/integration-principals",
        json={
            "name": "nope",
            "kind": "service",
            "scopes": ["patient:read"],
            "patient_scope": "none",
        },
        headers=_headers(secret),
    )
    assert manage.status_code == 403


@pytest.mark.asyncio
async def test_listing_is_owner_scoped_for_non_admins(async_client: AsyncClient):
    tenant_id = await create_tenant()
    owner_a, headers_a = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    owner_b, _headers_b = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    await _create_principal(owner_a, ["patient:read"], name="a", patient_scope="all")
    await _create_principal(owner_b, ["patient:read"], name="b", patient_scope="all")

    listing_a = await async_client.get("/api/v1/integration-principals", headers=headers_a)
    assert listing_a.status_code == 200
    assert {item["name"] for item in listing_a.json()} == {"a"}

    _admin, admin_headers = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    listing_admin = await async_client.get("/api/v1/integration-principals", headers=admin_headers)
    assert listing_admin.status_code == 200
    assert {"a", "b"} <= {item["name"] for item in listing_admin.json()}


@pytest.mark.asyncio
async def test_duplicate_active_principal_for_one_integration_is_refused():
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    integration = await _integration(tenant_id, owner.id, patient_id)

    async with AsyncSessionLocal() as db:
        await integration_principals.create_principal(
            db,
            tenant_id=tenant_id,
            owner=owner,
            name="one",
            integration_id=integration.id,
            scopes=["patient:read"],
            patient_scope="granted",
            patient_ids=[patient_id],
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(ValidationError) as exc:
            await integration_principals.create_principal(
                db,
                tenant_id=tenant_id,
                owner=owner,
                name="two",
                integration_id=integration.id,
                scopes=["patient:read"],
                patient_scope="granted",
                patient_ids=[patient_id],
            )
        assert "already exists" in str(exc.value)


@pytest.mark.asyncio
async def test_expired_principal_stops_resolving(async_client: AsyncClient):
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    row, secret = await _create_principal(
        owner,
        ["patient:read"],
        patient_scope="granted",
        patient_ids=[patient_id],
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert (await async_client.get("/api/v1/patients", headers=_headers(secret))).status_code == 200

    async with AsyncSessionLocal() as db:
        stored = await db.get(IntegrationPrincipal, row.id)
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(stored)
        await db.commit()

    assert (await async_client.get("/api/v1/patients", headers=_headers(secret))).status_code == 401


@pytest.mark.asyncio
async def test_system_admin_owned_principal_patient_list_is_scoped(async_client: AsyncClient):
    """F1: a SYSTEM_ADMIN-owned credential lists only its own patient reach.

    The list endpoint used to branch on ``role == SYSTEM_ADMIN`` *before* the
    scoped reach, so a PAT/integration principal with ``patient_scope``
    ``granted``/``none`` listed the whole tenant. A scoped principal now always
    answers its identity-and-owner intersection; only an unscoped human
    SYSTEM_ADMIN keeps the unrestricted (and tenant-override) path.
    """
    tenant_id = await create_tenant()
    owner, _ = await make_user_headers(role=Role.SYSTEM_ADMIN, tenant_id=tenant_id)
    reachable = await _patient(tenant_id, owner.id, name="Reachable")
    await _patient(tenant_id, owner.id, name="Hidden")

    _row, secret = await _create_principal(
        owner,
        ["patient:read"],
        patient_scope="granted",
        patient_ids=[reachable],
    )
    response = await async_client.get(
        f"/api/v1/patients?limit=50&user_id={owner.id}", headers=_headers(secret)
    )
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {str(reachable)}

    _none_row, none_secret = await _create_principal(
        owner,
        ["patient:read"],
        name="scope none",
        patient_scope="none",
    )
    empty = await async_client.get("/api/v1/patients", headers=_headers(none_secret))
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []


@pytest.mark.asyncio
async def test_read_only_principal_refuses_every_clinical_write_seam():
    """F2: a ``patient:read``-only principal cannot write through a service.

    Ingest/sync funnels call the canonical services directly (no route gate),
    so the permission is enforced at each service seam. A scoped principal
    passes the patient-access check for a patient it can *read*; the write
    permission is what stops it.
    """
    from datetime import date

    from app.extensions.sdk.catalog import CatalogProposal
    from app.schemas.clinical_event import ClinicalEventCreate
    from app.schemas.examination import ExaminationCreate
    from app.services.catalog.catalog_proposal_service import apply_proposal
    from app.services.clinical.clinical_event_service import create_event
    from app.services.clinical.examination_service import create_examination
    from app.services.integration.integration_ingest import ingest_documents

    tenant_id = await create_tenant()
    owner, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _patient(tenant_id, owner.id)
    integration = await _integration(tenant_id, owner.id, patient_id)
    _row, secret = await _create_principal(
        owner,
        ["patient:read"],
        patient_scope="granted",
        patient_ids=[patient_id],
    )
    actor = await integration_principals.resolve_principal(secret)
    assert actor is not None

    async with AsyncSessionLocal() as db:
        with pytest.raises(AuthorizationError):
            await create_event(
                db, actor, ClinicalEventCreate(patient_id=patient_id, title="read-only write")
            )
        with pytest.raises(AuthorizationError):
            await create_examination(
                db, actor, ExaminationCreate(patient_id=patient_id, examination_date=date.today())
            )
        with pytest.raises(AuthorizationError):
            await apply_proposal(
                db, actor, integration, CatalogProposal(kind="biomarker", payload={"name": "X"})
            )
        with pytest.raises(AuthorizationError):
            await ingest_documents(
                db,
                integration,
                [SimpleNamespace(content=b"x", filename="x.pdf", external_id="doc-1")],
                actor=actor,
            )

"""Central enforcement — the app-level route gate at HTTP level (ADR 0054 1b).

Real sign-ins through the single verification path, then:

- no/invalid token on a session route → 401 (the route's own answer);
- the role x route matrix: USER denied tenant-wide surfaces (403) but
  allowed on patient-scoped and self-service ones (hidden-404 stays with
  the resource layer); ADMIN/MANAGER/SYSTEM_ADMIN per the scope rules;
- public/auth/token classes unaffected (their mechanisms answer);
- an unresolved route fails closed with 403;
- ``/me/access`` mirrors the caller's permission set; ``/auth/me`` keeps
  the family ``PublicUser`` shape.
"""

import uuid

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.core.authz.enforce import (
    enforce_route_policy,
    get_optional_session_user,
    resolve_ws_policy_or_none,
)
from app.core.authz.policy import RouteClass
from app.core.authz.roles import permissions_for
from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyStructure
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.schemas.user import TokenData
from tests._auth_helpers import auth_headers, create_tenant, create_user, make_user_headers


def _fake_request(path: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    from types import SimpleNamespace

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
        "query_string": b"",
        "route": SimpleNamespace(path=path, endpoint=None, methods={"GET"}),
        "fastapi": {},
    }
    return Request(scope)


def _principal(role: Role) -> TokenData:
    """A gate-level principal (the gate reads role/scope facts only)."""
    return TokenData(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=role.value, sub="x")


# --- 401: the session class answers like get_current_user -------------------


@pytest.mark.asyncio
async def test_missing_token_is_401_not_403(async_client: AsyncClient):
    response = await async_client.get("/api/v1/patients")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_invalid_token_keeps_the_verifier_detail(async_client: AsyncClient):
    response = await async_client.get(
        "/api/v1/patients", headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
async def test_me_access_requires_a_session(async_client: AsyncClient):
    response = await async_client.get("/api/v1/me/access")
    assert response.status_code == 401


# --- 403: the role x route matrix -------------------------------------------


@pytest.mark.asyncio
async def test_user_lists_only_own_patients(async_client: AsyncClient):
    """``GET /patients`` is ``patient:read @ own`` (the Phase-6B mapping fix):
    the gate lets USER through and the handler filters to
    ``Patient.user_id == caller`` — own rows return, the same-tenant
    foreign patient never appears."""
    tenant_id = await create_tenant()
    user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    other_user = await create_user(role=Role.USER, tenant_id=tenant_id)
    own_id, foreign_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=own_id,
                tenant_id=tenant_id,
                user_id=user.id,
                name={"family": "Own", "given": ["Record"]},
                gender="UNKNOWN",
            )
        )
        session.add(
            Patient(
                id=foreign_id,
                tenant_id=tenant_id,
                user_id=other_user.id,
                name={"family": "Foreign", "given": ["Record"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()

    response = await async_client.get("/api/v1/patients", headers=headers)
    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids == {str(own_id)}
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_user_never_reaches_a_foreign_patient_detail(async_client: AsyncClient):
    """The detail route stays ``patient:read @ grant`` — the row-level
    answer stays with the resource layer: a real same-tenant patient owned
    by another member is 403 (today's ``check_patient_access``; the
    hidden-404 flattening is Phase 2) and a cross-tenant patient is a
    hidden 404. Neither leaks the row."""
    tenant_id = await create_tenant()
    _user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    other_tenant_id = await create_tenant()
    other_user = await create_user(role=Role.USER, tenant_id=other_tenant_id)
    same_tenant_id, cross_tenant_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=same_tenant_id,
                tenant_id=tenant_id,
                user_id=(await create_user(role=Role.USER, tenant_id=tenant_id)).id,
                name={"family": "Foreign", "given": ["Same"]},
                gender="UNKNOWN",
            )
        )
        session.add(
            Patient(
                id=cross_tenant_id,
                tenant_id=other_tenant_id,
                user_id=other_user.id,
                name={"family": "Foreign", "given": ["Cross"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()

    same_tenant = await async_client.get(f"/api/v1/patients/{same_tenant_id}", headers=headers)
    assert same_tenant.status_code == 403
    # Row-level denial: no gate marker — the SPA must not toast "access
    # denied" for a resource-layer ownership answer (Phase-6 revise).
    assert same_tenant.headers.get("x-authz-denied") is None
    cross_tenant = await async_client.get(f"/api/v1/patients/{cross_tenant_id}", headers=headers)
    assert cross_tenant.status_code == 404
    assert str(cross_tenant_id) not in cross_tenant.text


@pytest.mark.asyncio
async def test_user_passes_patient_scoped_paths_hidden_404_stays(async_client: AsyncClient):
    """A patient-scoped read needs only GRANT reach; USER's own covers it
    and the row-level ownership answer stays the resource layer's (404)."""
    _user, headers = await make_user_headers(role=Role.USER)
    response = await async_client.get(f"/api/v1/patients/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_user_self_service_surfaces_need_no_permission(async_client: AsyncClient):
    _user, headers = await make_user_headers(role=Role.USER)
    for path in ("/api/v1/me/access", "/api/v1/notifications/inbox", "/api/v1/settings/user"):
        response = await async_client.get(path, headers=headers)
        assert response.status_code == 200, path


@pytest.mark.asyncio
async def test_admin_reads_the_tenant_list(async_client: AsyncClient):
    _user, headers = await make_user_headers(role=Role.ADMIN)
    response = await async_client.get("/api/v1/patients", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_manager_denied_user_administration(async_client: AsyncClient):
    """user:read is ADMIN-side in the baseline — MANAGER's invite grant does
    not read the directory (the route-level pin stays for defense)."""
    _user, headers = await make_user_headers(role=Role.MANAGER)
    response = await async_client.get("/api/v1/users", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_manager_reads_the_tenant_patient_list(async_client: AsyncClient):
    _user, headers = await make_user_headers(role=Role.MANAGER)
    response = await async_client.get("/api/v1/patients", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_reads_the_audit_stream_tenant_scoped(async_client: AsyncClient):
    """ADR 0054 Phase 3 (program 8B): ``audit:read`` @ tenant is the admin
    viewer's floor — ADMIN reaches the stream scoped to their own tenant
    (the handler hides foreign tenants with 404); SYSTEM_ADMIN keeps the
    global feed."""
    _user, admin_headers = await make_user_headers(role=Role.ADMIN)
    own = await async_client.get("/api/v1/admin/audit", headers=admin_headers)
    assert own.status_code == 200
    assert own.json()["total"] >= 0
    foreign = await async_client.get(
        "/api/v1/admin/audit",
        headers=admin_headers,
        params={"tenant_id": str(uuid.uuid4())},
    )
    assert foreign.status_code == 404
    system_wide = await async_client.get(
        "/api/v1/admin/audit", headers=admin_headers, params={"all": "true"}
    )
    assert system_wide.status_code == 403


@pytest.mark.asyncio
async def test_system_admin_reads_the_global_and_tenant_audit(async_client: AsyncClient):
    user, headers = await make_user_headers(role=Role.SYSTEM_ADMIN)
    global_audit = await async_client.get("/api/v1/admin/audit", headers=headers)
    assert global_audit.status_code == 200
    tenant_audit = await async_client.get(
        f"/api/v1/admin/tenants/{user.tenant_id}/audit", headers=headers
    )
    assert tenant_audit.status_code == 200


@pytest.mark.asyncio
async def test_system_admin_passes_global_surfaces(async_client: AsyncClient):
    _user, headers = await make_user_headers(role=Role.SYSTEM_ADMIN)
    audit = await async_client.get("/api/v1/admin/audit", headers=headers)
    assert audit.status_code == 200
    tenants = await async_client.get("/api/v1/admin/tenants", headers=headers)
    assert tenants.status_code == 200


# --- the table-driven matrix -------------------------------------------------

#: (role, method, path) → the gate must NOT 403 (any other answer — 200,
#: 404/422 from the handler — proves the gate let it through).
_MATRIX_ALLOWED = [
    (Role.USER, "GET", "/api/v1/patients"),
    (Role.USER, "GET", "/api/v1/examinations"),
    (Role.USER, "GET", "/api/v1/documents"),
    (Role.USER, "GET", "/api/v1/allergies/catalog"),
    (Role.USER, "GET", f"/api/v1/allergies/patient/{uuid.uuid4()}"),
    (Role.USER, "GET", "/api/v1/notifications/unread-count"),
    (Role.USER, "GET", "/api/v1/setup/checklist"),
    (Role.MANAGER, "GET", "/api/v1/patients"),
    (Role.MANAGER, "GET", "/api/v1/export/jobs"),
    (Role.MANAGER, "POST", f"/api/v1/catalogs/anatomy/{uuid.uuid4()}/promote"),
    (Role.ADMIN, "GET", "/api/v1/patients"),
    (Role.ADMIN, "GET", "/api/v1/settings/tenant"),
    (Role.ADMIN, "GET", "/api/v1/oauth/clients"),
    (Role.ADMIN, "GET", "/api/v1/catalogs"),
    (Role.ADMIN, "GET", "/api/v1/admin/audit"),
    (Role.ADMIN, "POST", f"/api/v1/catalogs/anatomy/{uuid.uuid4()}/promote"),
    (Role.SYSTEM_ADMIN, "GET", "/api/v1/admin/tenants"),
    (Role.SYSTEM_ADMIN, "GET", "/api/v1/admin/integrations"),
    (Role.SYSTEM_ADMIN, "GET", "/api/v1/extensions/sources"),
]

#: (role, method, path) → 403: the role lacks the permission or its scope.
_MATRIX_DENIED = [
    (Role.USER, "GET", "/api/v1/users"),
    (Role.USER, "GET", "/api/v1/data-lifecycle/erasures"),
    (Role.USER, "GET", "/api/v1/admin/tenants"),
    (Role.USER, "GET", "/api/v1/settings/tenant"),
    (Role.USER, "POST", "/api/v1/import/fhir"),
    (Role.USER, "GET", "/api/v1/admin/audit"),
    (Role.USER, "POST", f"/api/v1/catalogs/anatomy/{uuid.uuid4()}/promote"),
    (Role.MANAGER, "GET", "/api/v1/users"),
    (Role.MANAGER, "GET", "/api/v1/admin/audit"),
    (Role.MANAGER, "POST", "/api/v1/admin/tenants"),
    (Role.MANAGER, "GET", "/api/v1/extensions/sources"),
]


@pytest.mark.parametrize(("role", "method", "path"), _MATRIX_ALLOWED)
@pytest.mark.asyncio
async def test_matrix_allows(async_client: AsyncClient, role: Role, method: str, path: str):
    _user, headers = await make_user_headers(role=role)
    response = await async_client.request(method, path, headers=headers)
    assert response.status_code not in (401, 403), (
        f"{role.value} {method} {path}: gate denied ({response.status_code})"
    )


@pytest.mark.parametrize(("role", "method", "path"), _MATRIX_DENIED)
@pytest.mark.asyncio
async def test_matrix_denies(async_client: AsyncClient, role: Role, method: str, path: str):
    _user, headers = await make_user_headers(role=role)
    response = await async_client.request(method, path, headers=headers)
    assert response.status_code == 403, (
        f"{role.value} {method} {path}: expected 403, got {response.status_code}"
    )
    # The gate marks its denials (the SPA's toast discriminator).
    assert response.headers.get("x-authz-denied") == "1"


@pytest.mark.asyncio
async def test_manager_promotes_a_user_scope_catalog_row_to_tenant(async_client: AsyncClient):
    """``catalog:promote @ tenant`` admits MANAGER (Phase-6 revise):
    ``CatalogAccessPolicy.check_promote`` and the promote UI both advertise
    MANAGER user↔tenant transitions, so the gate must let the same caller
    through — a real user-scope row moves to tenant with 200. USER is
    denied at the gate (marker header, uniform detail)."""
    tenant_id = await create_tenant()
    slug = f"gate-promote-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        node = AnatomyStructure(slug=slug, name="Gate Node", scope="user", tenant_id=tenant_id)
        session.add(node)
        await session.commit()
        await session.refresh(node)
        item_id = str(node.id)

    _manager, manager_headers = await make_user_headers(role=Role.MANAGER, tenant_id=tenant_id)
    promoted = await async_client.post(
        f"/api/v1/catalogs/anatomy/{item_id}/promote",
        json={"scope": "tenant"},
        headers=manager_headers,
    )
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert body["scope"] == "tenant"

    _user, user_headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    denied = await async_client.post(
        f"/api/v1/catalogs/anatomy/{item_id}/promote",
        json={"scope": "user"},
        headers=user_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Access denied"
    assert denied.headers.get("x-authz-denied") == "1"


# --- public / token classes are untouched ------------------------------------


@pytest.mark.asyncio
async def test_public_and_auth_bootstrap_surfaces_are_not_gated(async_client: AsyncClient):
    health = await async_client.get("/health")
    assert health.status_code == 200
    config = await async_client.get("/api/v1/config/public")
    assert config.status_code == 200
    metadata = await async_client.get("/api/v1/fhir/R4/metadata")
    assert metadata.status_code == 200
    setup_status = await async_client.get("/api/v1/auth/setup-status")
    assert setup_status.status_code == 200
    login = await async_client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert login.status_code in (401, 422, 423)


@pytest.mark.asyncio
async def test_presign_download_stays_a_token_route(async_client: AsyncClient):
    """The presign class verifies its own download token — no session 401
    from the gate; the route's own mechanism answers."""
    response = await async_client.get(f"/api/v1/documents/{uuid.uuid4()}/download")
    assert response.status_code in (401, 404)


# --- fail closed --------------------------------------------------------------


@pytest.mark.asyncio
async def test_unresolved_route_fails_closed():
    """A session route that resolves to no policy is denied with 403 —
    never silently allowed (the CI gate catches it first; this is the wall)."""
    with pytest.raises(Exception) as excinfo:
        await enforce_route_policy(_fake_request("/api/v1/brand-new"), _principal(Role.USER))
    assert getattr(excinfo.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_unknown_role_fails_closed():
    principal = TokenData(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="SOMETHING_ELSE", sub="x"
    )
    with pytest.raises(Exception) as excinfo:
        await enforce_route_policy(_fake_request("/api/v1/patients"), principal)
    assert getattr(excinfo.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_scope_escalation_fails_closed():
    """USER holds lifecycle:request at OWN; the tenant-wide erasures list
    (TENANT) is not covered — the scope check, not the permission check,
    denies."""
    with pytest.raises(Exception) as excinfo:
        await enforce_route_policy(
            _fake_request("/api/v1/data-lifecycle/erasures"), _principal(Role.USER)
        )
    assert getattr(excinfo.value, "status_code", None) == 403


# --- the WebSocket policy share ----------------------------------------------


def test_ws_policy_resolves_auth_only_for_both_channels():
    for path in ("/api/v1/ws/tasks", "/api/v1/ws/notifications"):
        policy = resolve_ws_policy_or_none(path)
        assert policy is not None
        assert policy.route_class is RouteClass.WEBSOCKET
        assert policy.permission is None


def test_ws_policy_fails_closed_for_an_unknown_path():
    assert resolve_ws_policy_or_none("/api/v1/ws/unknown") is None


# --- /me/access and /auth/me ---------------------------------------------------


@pytest.mark.asyncio
async def test_me_access_mirrors_the_permission_set(async_client: AsyncClient):
    user, headers = await make_user_headers(role=Role.USER)
    response = await async_client.get("/api/v1/me/access", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["id"] == str(user.id)
    assert payload["user"]["email"] == user.email
    assert payload["user"]["role"] == Role.USER.value
    assert payload["roles"] == [Role.USER.value]
    assert payload["permissions"] == sorted(p.value for p in permissions_for(Role.USER))
    assert payload["tenant"]["id"] == str(user.tenant_id)
    assert payload["patient_grants"] == []
    assert payload["pending_grants"] == []


@pytest.mark.asyncio
async def test_me_access_admin_sees_user_administration(async_client: AsyncClient):
    _user, headers = await make_user_headers(role=Role.ADMIN)
    response = await async_client.get("/api/v1/me/access", headers=headers)
    permissions = response.json()["permissions"]
    assert "user:invite" in permissions
    assert "audit:read" in permissions


@pytest.mark.asyncio
async def test_auth_me_keeps_the_family_shape(async_client: AsyncClient):
    user, headers = await make_user_headers(role=Role.USER)
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user.id)
    assert set(body) == {"id", "email", "full_name", "is_admin", "is_active"}


# --- the cache contract ---------------------------------------------------------


@pytest.mark.asyncio
async def test_optional_session_user_caches_on_request_state():
    user = await create_user(role=Role.USER)
    headers = await auth_headers(user)
    token = headers["Authorization"].removeprefix("Bearer ")
    request = _fake_request(
        "/api/v1/patients", headers=[(b"authorization", f"Bearer {token}".encode())]
    )
    first = await get_optional_session_user(request)
    assert first is not None and str(first.user_id) == str(user.id)
    assert request.state.session_user is first
    second = await get_optional_session_user(request)
    assert second is first


@pytest.mark.asyncio
async def test_optional_session_user_caches_the_verification_error():
    request = _fake_request("/api/v1/patients", headers=[(b"authorization", b"Bearer not-a-token")])
    assert await get_optional_session_user(request) is None
    assert request.state.session_auth_error.status_code == 401


@pytest.mark.asyncio
async def test_gate_reuses_the_cached_principal_not_a_second_verification():
    """get_current_user returns the gate's cached principal — one session
    verification per request (the DB/Redis round trip happens once)."""
    user = await create_user(role=Role.USER)
    headers = await auth_headers(user)
    from app.core.security import get_current_user

    request = _fake_request(
        "/api/v1/patients",
        headers=[(b"authorization", headers["Authorization"].encode())],
    )
    gate_principal = await get_optional_session_user(request)
    assert gate_principal is not None
    route_principal = await get_current_user(request)
    assert route_principal is gate_principal

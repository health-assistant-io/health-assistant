"""Unit tests for ``app.services.integration_actor``.

The actor resolver is what lets Celery / webhook / api-proxy contexts call
the same service-layer functions (``clinical_event_service.create_event``,
the future ``examination_service.create_examination``, etc.) that
interactive UI requests call — by deriving a ``TokenData`` from the
``UserIntegration``'s owning user.

These tests use a minimal fake session (no DB) so they exercise the resolver
logic in isolation.
"""
import pytest
from uuid import uuid4

from app.core.errors import NotFoundError
from app.schemas.user import TokenData
from app.services.integration_actor import resolve_integration_actor


class _FakeUser:
    """Stand-in for ``UserModel`` — only the attributes the resolver reads."""

    def __init__(self, *, user_id, tenant_id, role_value, email="owner@example.com"):
        self.id = user_id
        self.tenant_id = tenant_id
        # UserModel.role is a SQLEnum(Role); mirror the ``.value`` access pattern.
        self.role = type("R", (), {"value": role_value})()
        self.email = email


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Captures the executed query so the test can assert it targeted
    ``UserModel``; returns the constructor-provided row (or ``None``)."""

    def __init__(self, row):
        self._row = row
        self.executed_query = None

    async def execute(self, query):
        self.executed_query = query
        return _FakeResult(self._row)


class _FakeIntegration:
    """Stand-in for ``UserIntegration`` — only the attributes the resolver reads."""

    def __init__(self, *, integration_id=None, user_id=None, tenant_id=None):
        self.id = integration_id or uuid4()
        self.user_id = user_id
        self.tenant_id = tenant_id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_actor_returns_token_data_with_owner_fields():
    """The resolver must produce a TokenData carrying the owner's id, tenant,
    role, and email — so service-layer checks (``check_*_access``) and
    ``AuditMixin.created_by`` populate exactly as they would for an
    interactive UI request from that user."""
    owner_id = uuid4()
    tenant_id = uuid4()
    integration = _FakeIntegration(user_id=owner_id, tenant_id=tenant_id)
    user = _FakeUser(user_id=owner_id, tenant_id=tenant_id, role_value="ADMIN")
    db = _FakeSession(user)

    actor = await resolve_integration_actor(db, integration)

    assert isinstance(actor, TokenData)
    assert actor.user_id == owner_id
    assert actor.tenant_id == tenant_id
    assert actor.role == "ADMIN"
    assert actor.sub == "owner@example.com"  # TokenData exposes email via ``sub``
    assert actor.is_service_account is False


@pytest.mark.asyncio
async def test_resolve_actor_uses_user_role_not_integration_tenant():
    """The role is re-read from ``UserModel`` on every call, not cached on
    the integration — so a role change (ADMIN → USER) takes effect on the
    next sync. The tenant_id also comes from the user, not the integration
    row, defending against a stale tenant if the user was moved."""
    owner_id = uuid4()
    user_tenant = uuid4()
    integration_tenant = uuid4()  # different from the user's current tenant
    integration = _FakeIntegration(
        user_id=owner_id, tenant_id=integration_tenant
    )
    user = _FakeUser(user_id=owner_id, tenant_id=user_tenant, role_value="USER")
    db = _FakeSession(user)

    actor = await resolve_integration_actor(db, integration)

    assert actor.tenant_id == user_tenant, (
        "tenant_id must come from the user row, not the integration row — "
        "defends against the owner having been moved to a new tenant after "
        "the integration was connected"
    )
    assert actor.role == "USER"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_actor_raises_not_found_if_owner_deleted():
    """If the owning user row is gone (hard delete / FK cascade) the resolver
    must raise :class:`NotFoundError` so the caller can surface it as an
    integration ERROR — service-layer writes cannot proceed without a valid
    owning identity."""
    integration = _FakeIntegration(user_id=uuid4(), tenant_id=uuid4())
    db = _FakeSession(row=None)  # user row not found

    with pytest.raises(NotFoundError, match="no longer exists"):
        await resolve_integration_actor(db, integration)


@pytest.mark.asyncio
async def test_resolve_actor_raises_validation_error_if_integration_has_no_user():
    """A malformed integration row with ``user_id=None`` can't resolve to an
    identity — the resolver must refuse loudly rather than querying
    ``UserModel.id IS NULL`` (which would always miss)."""
    integration = _FakeIntegration(user_id=None, tenant_id=uuid4())
    db = _FakeSession(row=None)

    with pytest.raises(NotFoundError, match="no owning user_id"):
        await resolve_integration_actor(db, integration)
    # The DB must NOT have been queried in this case.
    assert db.executed_query is None


# ---------------------------------------------------------------------------
# Audit / RBAC compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_actor_passes_check_patient_access_shape():
    """Sanity check that the produced ``TokenData`` is shaped correctly for
    the canonical ``check_patient_access`` helper. We don't invoke the real
    helper (that needs a DB); we just confirm the fields it reads are
    populated with the right types."""
    owner_id = uuid4()
    tenant_id = uuid4()
    integration = _FakeIntegration(user_id=owner_id, tenant_id=tenant_id)
    user = _FakeUser(user_id=owner_id, tenant_id=tenant_id, role_value="USER")
    db = _FakeSession(user)

    actor = await resolve_integration_actor(db, integration)

    # check_patient_access reads: actor.user_id, actor.tenant_id, actor.role
    assert isinstance(actor.user_id, type(owner_id))
    assert isinstance(actor.tenant_id, type(tenant_id))
    assert actor.role in ("USER", "MANAGER", "ADMIN", "SYSTEM_ADMIN")

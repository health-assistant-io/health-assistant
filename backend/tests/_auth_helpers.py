"""Identity helpers for tests (plan 16 H1 — contract claims, §8).

Every token that reaches a live verifier must satisfy the whole chain:
contract claims (``iss``/``sub``/``token_kind``/``jti``/``ver``/``auth_mode``),
a real ``users`` row (``is_active`` + ``token_version`` == ``ver``), and a
registered session jti (the Redis hot revocation layer). These helpers
mint exactly that — never call ``create_session_access_token`` with
hand-rolled claims unless the test is specifically about the verifier.

``sign_in`` goes through ``auth_session_service.issue_session`` so tests
also get the ``auth_sessions`` family row (device list + refresh rotation).
"""

from __future__ import annotations

import uuid
from typing import Optional

from app.core.database import AsyncSessionLocal
from app.core.security import (
    AUTH_MODE_DEMO,
    AUTH_MODE_PASSWORD,
    get_password_hash,
)
from app.models.enums import Role
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.auth_session_service import issue_session

__all__ = [
    "AUTH_MODE_DEMO",
    "AUTH_MODE_PASSWORD",
    "auth_headers",
    "create_tenant",
    "create_user",
    "make_user_headers",
    "sign_in",
]


def _role(role) -> Role:
    return role if isinstance(role, Role) else Role(role)


async def create_tenant(name: str = "Test Tenant") -> uuid.UUID:
    """Insert a tenant row (unique slug per call) and return its id."""
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=name, slug=f"test-tenant-{tenant_id}")
        )
        await session.commit()
    return tenant_id


async def create_user(
    role=Role.SYSTEM_ADMIN,
    tenant_id: Optional[uuid.UUID] = None,
    email: Optional[str] = None,
    password: Optional[str] = None,
    is_active: bool = True,
    full_name: str = "",
    unique: bool = True,
    user_id: Optional[uuid.UUID] = None,
) -> UserModel:
    """Insert a real user row (the verifier requires one) and return it.

    ``unique=True`` (default) makes the email collide-proof across tests by
    suffixing the local part (``alice+u1a2b3c4d@test.local``) — callers
    that must control the exact address (login/invite flows) pass
    ``unique=False`` and manage uniqueness themselves. ``user_id`` pins a
    specific id for tests that assert ownership stamps.
    """
    if tenant_id is None:
        tenant_id = await create_tenant()
    if email is None:
        email = f"user-{uuid.uuid4().hex[:12]}@test.local"
    elif unique:
        local, _, domain = email.partition("@")
        email = f"{local}+u{uuid.uuid4().hex[:8]}@{domain or 'test.local'}"
    kwargs = dict(
        email=email,
        password_hash=get_password_hash(password) if password else None,
        full_name=full_name,
        role=_role(role),
        tenant_id=str(tenant_id),
        is_active=is_active,
        settings={},
    )
    if user_id is not None:
        kwargs["id"] = user_id
    user = UserModel(**kwargs)
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def sign_in(user: UserModel, *, auth_mode: str = AUTH_MODE_PASSWORD, client_label: str = "pytest"):
    """A full sign-in against the live user row (family + tokens + Redis)."""
    return await issue_session(user, auth_mode=auth_mode, client_label=client_label)


async def auth_headers(
    user: UserModel, *, auth_mode: str = AUTH_MODE_PASSWORD, extra_claims: dict | None = None
) -> dict:
    """Bearer headers carrying a verified session token for ``user``."""
    issued = await issue_session(
        user,
        auth_mode=auth_mode,
        client_label="pytest",
        extra_claims=extra_claims,
        with_refresh=False,
    )
    return {"Authorization": f"Bearer {issued.access_token}"}


async def make_user_headers(
    role=Role.SYSTEM_ADMIN,
    tenant_id: Optional[uuid.UUID] = None,
    **kwargs,
) -> tuple[UserModel, dict]:
    """(user, headers) for one fresh account — the common test shape."""
    user = await create_user(role=role, tenant_id=tenant_id, **kwargs)
    return user, await auth_headers(user)


_SWITCH_CLAIMS = (
    "original_tenant_id",
    "original_user_id",
    "switched",
    "scoped_tenant_id",
    "tenant_id",
)


async def headers_for_claims(claims: dict) -> dict:
    """Turn a legacy-style claims dict into a **verified** sign-in.

    Creates the real user row the live verifier demands (is_active + ver)
    and mints contract-claim headers through the real issuance path.
    Extra product claims (switched-set / tenant override) are carried over
    as-is. Use this at old call sites that used to hand-roll
    ``create_access_token({...})``.
    """
    raw_sub = str(claims.get("sub") or "")
    user = await create_user(
        role=claims.get("role", Role.USER.value),
        tenant_id=uuid.UUID(str(claims["tenant_id"])) if claims.get("tenant_id") else None,
        email=raw_sub if "@" in raw_sub else None,
    )
    extra = {k: claims[k] for k in _SWITCH_CLAIMS if k in claims}
    return await auth_headers(user, extra_claims=extra or None)

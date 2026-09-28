"""Contract drift gates — the identity-auth §18 case health did not yet
assert product-side (guideline §18; the kit's ``CONTRACT_CASES`` is the
checklist).

Health keeps its own identity core (plan 16 §5.10 — the contract + test
kit is the coupling), so these run §18 through health's real verifiers.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.core import token_store
from app.core.security import create_session_access_token
from tests._auth_helpers import AUTH_MODE_PASSWORD, create_tenant, create_user, sign_in

pytestmark = pytest.mark.contract


@pytest.mark.asyncio
async def test_expired_access_token_is_401_and_refresh_recovers(
    async_client: AsyncClient,
):
    """§18.2 — an expired session JWT stops authenticating; the refresh
    path mints a fresh working pair (rotation itself is asserted in
    ``test_auth_cookies.py``)."""
    tenant = await create_tenant()
    user = await create_user(tenant_id=tenant)
    token, jti = create_session_access_token(
        {
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
            "email": user.email,
            "ver": int(getattr(user, "token_version", 1) or 1),
            "auth_mode": AUTH_MODE_PASSWORD,
        },
        expires_delta=timedelta(seconds=-10),
    )
    await token_store.register_session(str(user.id), jti, 600)

    expired = await async_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert expired.status_code == 401

    issued = await sign_in(user)
    recovered = await async_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {issued.access_token}"}
    )
    assert recovered.status_code == 200

"""Audit 2026-09-11 S-7 — demo-token claim hygiene.

A session token minted with ``auth_mode="demo"`` (the ``/auth/demo-login``
stamp, §13) must stop authenticating the moment the instance stops being a
demo (``instance_settings.demo_mode`` false); password tokens are
unaffected by the demo flag.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core import instance_state, token_store
from app.core.security import (
    AUTH_MODE_DEMO,
    AUTH_MODE_PASSWORD,
    create_session_access_token,
    get_current_user,
)

from ._auth_helpers import create_user

# Every test here implements identity-auth §18.11 (demo token claim) —
# the family contract drift gate.
pytestmark = pytest.mark.contract


async def _mint_session_token(user, *, auth_mode: str) -> str:
    """A live session token for ``user`` stamped with ``auth_mode``."""
    token, jti = create_session_access_token(
        {
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
            "email": user.email,
            "ver": int(getattr(user, "token_version", 1) or 1),
            "auth_mode": auth_mode,
        }
    )
    await token_store.register_session(str(user.id), jti, 600)
    return token


def _state_with(demo_mode: bool) -> AsyncMock:
    return AsyncMock(
        return_value=SimpleNamespace(
            auth_mode=instance_state.AUTH_MODE_AUTHENTICATED, demo_mode=demo_mode
        )
    )


@pytest.mark.asyncio
async def test_demo_token_accepted_with_demo_mode():
    user = await create_user()
    token = await _mint_session_token(user, auth_mode=AUTH_MODE_DEMO)
    with patch.object(
        instance_state, "get_state", new=_state_with(demo_mode=True)
    ):
        token_data = await get_current_user(token)
    assert str(token_data.user_id) == str(user.id)


@pytest.mark.asyncio
async def test_demo_token_rejected_without_demo_mode():
    user = await create_user()
    token = await _mint_session_token(user, auth_mode=AUTH_MODE_DEMO)
    with patch.object(
        instance_state, "get_state", new=_state_with(demo_mode=False)
    ):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_regular_token_unaffected():
    user = await create_user()
    token = await _mint_session_token(user, auth_mode=AUTH_MODE_PASSWORD)
    with patch.object(
        instance_state, "get_state", new=_state_with(demo_mode=False)
    ):
        token_data = await get_current_user(token)
    assert str(token_data.user_id) == str(user.id)

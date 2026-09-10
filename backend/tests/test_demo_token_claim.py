"""Audit 2026-09-11 S-7 — demo-token claim hygiene.

A token minted by ``/auth/demo-login`` carries a ``demo`` claim. It must stop
authenticating the moment ``DEMO_MODE`` is turned off; turning off an
instance's demo mode revokes all demo tokens on their next use.
"""

from __future__ import annotations

import uuid
import jwt
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import create_refresh_token, get_current_user

CLAIMS = {
    "sub": "demo@healthassistant.local",
    "user_id": str(uuid.uuid4()),
    "tenant_id": str(uuid.uuid4()),
    "role": "USER",
    "demo": True,
    "type": "access",
}


def _mint_demo_token() -> str:
    payload = dict(CLAIMS)
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _mint_nondemo_token() -> str:
    payload = {k: v for k, v in CLAIMS.items() if k != "demo"}
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


@pytest.mark.asyncio
async def test_demo_token_accepted_with_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    token_data = await get_current_user(_mint_demo_token())
    assert str(token_data.user_id) == CLAIMS["user_id"]


@pytest.mark.asyncio
async def test_demo_token_rejected_without_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_mint_demo_token())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_regular_token_unaffected(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    token_data = await get_current_user(_mint_nondemo_token())
    assert str(token_data.user_id) == CLAIMS["user_id"]

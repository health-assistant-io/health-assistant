"""Shared seed-stage helpers for the test suite (ADR 0056 transaction law).

The seeders take a required session and never commit — tests that exercise a
stage end-to-end run it in its own session and commit once via
:func:`seed_with_commit`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.database import AsyncSessionLocal


async def seed_with_commit(stage: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one seed stage in a fresh session and commit (nothing partial).

    ``stage`` is a ``SeedService`` seed method (or ``seed_all``); any extra
    args are forwarded after the session.
    """
    async with AsyncSessionLocal() as session:
        result = await stage(session, *args, **kwargs)
        await session.commit()
        return result

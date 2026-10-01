"""Instance access-mode facts (identity-auth §4) — DB-authoritative.

Two orthogonal switches; this module owns the second:

* entrypoint (``desktop`` | ``server``) — derived from launch; health is a
  Class S server product, so the entrypoint is always ``server`` and the
  instance never runs ``open`` (§4.4).
* ``instance_settings.auth_mode`` (``open`` | ``authenticated``) and the
  companion ``demo_mode`` (§13) — **instance facts stored in the
  database**, written only at initialization:

  - on an **empty DB** the env inputs ``HA_AUTH_MODE`` / ``HA_DEMO_MODE``
    are consumed once (``initialize``), then ignored forever;
  - post-init env flips are ignored with a loud warning — the DB wins;
  - unknown / missing stored values fail **closed**: ``authenticated``,
    ``demo_mode=false``.

Reads are per-request and state-derived (``get_state``) — never a
boot-time snapshot (§4.2). Enforcement helpers mirror the kit's
``nx_auth.instance`` semantics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.instance_setting_model import InstanceSettingModel

logger = logging.getLogger(__name__)

AUTH_MODE_KEY = "auth_mode"
DEMO_MODE_KEY = "demo_mode"

AUTH_MODE_OPEN = "open"
AUTH_MODE_AUTHENTICATED = "authenticated"
VALID_AUTH_MODES = (AUTH_MODE_OPEN, AUTH_MODE_AUTHENTICATED)


@dataclass(frozen=True)
class InstanceState:
    """What the database says this instance is (identity-auth §4).

    ``auth_mode=None`` means unknown/missing — unknown always evaluates
    as ``authenticated`` (fail-closed).
    """

    auth_mode: str | None
    demo_mode: bool


def parse_auth_mode(raw: str | None) -> str | None:
    """Stored value → mode; anything unparsable becomes None (fail-closed)."""
    if raw == AUTH_MODE_OPEN:
        return AUTH_MODE_OPEN
    if raw == AUTH_MODE_AUTHENTICATED:
        return AUTH_MODE_AUTHENTICATED
    return None


def effective_auth_mode(state: InstanceState) -> str:
    """The mode enforcement must assume: only an explicit ``open`` is open."""
    return AUTH_MODE_OPEN if state.auth_mode == AUTH_MODE_OPEN else AUTH_MODE_AUTHENTICATED


async def get_state() -> InstanceState:
    """Fresh, per-request read of the instance's identity facts.

    Degrades **closed**: if the database is unreachable, the instance is
    treated as ``authenticated`` with ``demo_mode=false``.
    """
    if not DATABASE_AVAILABLE:
        return InstanceState(auth_mode=None, demo_mode=False)
    try:
        async with AsyncSessionLocal() as session:
            rows: Any = await session.execute(
                select(InstanceSettingModel.key, InstanceSettingModel.value).where(
                    InstanceSettingModel.key.in_([AUTH_MODE_KEY, DEMO_MODE_KEY])
                )
            )
            values = dict(rows.all())
    except Exception as e:  # fail closed — never fail open
        logger.warning("instance_settings unavailable, failing closed: %s", e)
        return InstanceState(auth_mode=None, demo_mode=False)
    return InstanceState(
        auth_mode=parse_auth_mode(values.get(AUTH_MODE_KEY)),
        demo_mode=str(values.get(DEMO_MODE_KEY, "")).strip().lower() == "true",
    )


async def demo_mode_enabled() -> bool:
    """Convenience for demo-admission checks (§13)."""
    return (await get_state()).demo_mode


async def _get_value(session, key: str) -> str | None:
    result = await session.execute(
        select(InstanceSettingModel.value).where(InstanceSettingModel.key == key)
    )
    return result.scalar_one_or_none()


async def _set_value(session, key: str, value: str) -> None:
    existing = await session.execute(
        select(InstanceSettingModel).where(InstanceSettingModel.key == key)
    )
    row = existing.scalars().first()
    if row is not None:
        row.value = value
    else:
        session.add(InstanceSettingModel(key=key, value=value))


async def initialize() -> None:
    """Init-only write of the instance facts (identity-auth §4).

    Consumes ``HA_AUTH_MODE`` / ``HA_DEMO_MODE`` **only when initializing an
    empty database** (no ``instance_settings.auth_mode`` row and no user
    rows). Everywhere else it is a no-op besides the loud post-init
    mismatch warning: the env may never flip a running instance.

    Server entrypoint rule (§4.4): health never runs ``open`` — an
    ``HA_AUTH_MODE=open`` input is refused with a warning and recorded as
    ``authenticated``.
    """
    from app.core.config import settings
    from app.models.user_model import UserModel

    if not DATABASE_AVAILABLE:
        logger.warning("instance_settings init skipped — database unavailable")
        return

    async with AsyncSessionLocal() as session:
        existing_mode = await _get_value(session, AUTH_MODE_KEY)
        user_count: Any = (await session.execute(select(UserModel.id).limit(1))).first()

        if existing_mode is not None:
            # Post-init: env flips are ignored — DB is authoritative (§4.1).
            stored_demo = await _get_value(session, DEMO_MODE_KEY)
            _warn_on_env_mismatch(existing_mode, stored_demo)
            return

        if user_count is not None:
            # DB already has identities but no facts row (upgraded install):
            # never mint facts from env post-init. Reads fail closed.
            logger.warning(
                "instance_settings has no auth_mode row on a non-empty DB — "
                "leaving it unset (fails closed to authenticated). Set the "
                "facts via the first-boot path or insert them manually."
            )
            return

        mode = str(settings.HA_AUTH_MODE or "").strip().lower()
        if mode not in VALID_AUTH_MODES:
            logger.warning(
                "HA_AUTH_MODE=%r is not a known mode (%s) — recording "
                "'authenticated' (fail-closed).",
                settings.HA_AUTH_MODE,
                ", ".join(VALID_AUTH_MODES),
            )
            mode = AUTH_MODE_AUTHENTICATED
        elif mode == AUTH_MODE_OPEN:
            # §4.4: server never runs open.
            logger.warning(
                "HA_AUTH_MODE=open is refused on a server entrypoint — "
                "recording 'authenticated' (identity-auth §4.4)."
            )
            mode = AUTH_MODE_AUTHENTICATED

        await _set_value(session, AUTH_MODE_KEY, mode)
        await _set_value(session, DEMO_MODE_KEY, "true" if settings.HA_DEMO_MODE else "false")
        await session.commit()
        logger.info(
            "instance_settings initialized: auth_mode=%s demo_mode=%s",
            mode,
            "true" if settings.HA_DEMO_MODE else "false",
        )


def _warn_on_env_mismatch(stored_mode: str, stored_demo: str | None) -> None:
    """Loud warning when the post-init env disagrees with the DB facts."""
    from app.core.config import settings

    env_mode = str(settings.HA_AUTH_MODE or "").strip().lower()
    if env_mode and env_mode != stored_mode:
        logger.warning(
            "HA_AUTH_MODE=%r disagrees with instance_settings.auth_mode=%r — "
            "the env is init-only and IGNORED post-init (identity-auth §4.1). "
            "The database value stays authoritative.",
            settings.HA_AUTH_MODE,
            stored_mode,
        )
    env_demo = "true" if settings.HA_DEMO_MODE else "false"
    if stored_demo is not None and env_demo != str(stored_demo).strip().lower():
        logger.warning(
            "HA_DEMO_MODE=%r disagrees with instance_settings.demo_mode=%r — "
            "the env is init-only and IGNORED post-init (identity-auth §13). "
            "The database value stays authoritative.",
            env_demo,
            stored_demo,
        )

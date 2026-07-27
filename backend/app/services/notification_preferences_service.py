"""Unified read/write contract over the two notification-preference stores.

Hides the storage split behind one service so callers (the
``/notifications/preferences`` endpoints, the kind-mute UI) never need to
know which backend a kind lives in:

* ``source:*``  and ``channel:*`` kinds → tiered settings registry
  (``notifications.sources.{X}`` / ``notifications.channels.{X}``), resolved
  USER > TENANT > SYSTEM > default and mutated at the USER level via
  :class:`SettingsService`.
* ``integration:{iid}:{tid}`` kinds → ad-hoc JSONB on ``UserModel.settings``
  (``notifications.integration.{iid}.{tid}``). These keys are dynamic (driven
  by each provider's declared types) so they aren't in the tiered registry.

The ``kind_id`` is the only contract; :class:`NotificationPreferencesService`
routes reads + writes to the right store.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import NotFoundError, ValidationError
from app.models.user_model import UserModel
from app.schemas.settings import SettingLevel
from app.services.notification_kind_registry import (
    NotificationKindMeta,
    enumerate_for_user,
)
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class NotificationPreferencesService:
    """Read + write notification preferences by ``kind_id``."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_all(
        self,
        user_id: UUID,
        tenant_id: Optional[UUID],
        integration_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """All kinds for ``user_id`` with their current enabled state.

        When ``integration_id`` is set, only kinds for that instance are
        returned (powers the per-instance notifications tab at
        ``/settings/integrations/:id?tab=notifications``).
        """
        metas = await enumerate_for_user(self.db, user_id)

        # Tiered effective values (sources + channels).
        tiered_values, _ = await SettingsService(self.db).resolve_effective(
            user_id, tenant_id
        )
        # Raw user JSONB (integration per-instance keys are not in the tiered
        # registry, so resolve_effective doesn't surface them).
        user_settings = await self._load_user_settings(user_id)

        out: list[dict[str, Any]] = []
        for meta in metas:
            if integration_id is not None and not meta.kind_id.startswith(
                f"integration:{integration_id}:"
            ):
                continue
            enabled = self._resolve_state(meta, tiered_values, user_settings)
            row = meta.to_dict()
            row["enabled"] = enabled
            out.append(row)
        return out

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def set(
        self,
        user_id: UUID,
        tenant_id: Optional[UUID],
        kind_id: str,
        enabled: bool,
    ) -> NotificationKindMeta:
        """Set ``kind_id`` to ``enabled`` for ``user_id``.

        Validates the kind exists for the user (404) and is mutable when
        disabling (400). Routes the write to the right store. Re-enabling an
        integration kind removes the override so it falls back to the
        provider default.
        """
        meta = await self._find_kind_for_user(user_id, kind_id)
        if meta is None:
            raise NotFoundError(f"Unknown notification kind: {kind_id}")
        if not enabled and not meta.mutable:
            raise ValidationError(
                f"Notification kind '{kind_id}' cannot be disabled"
            )

        if kind_id.startswith("source:"):
            await self._set_tiered(
                user_id,
                tenant_id,
                f"notifications.sources.{kind_id.split(':', 1)[1]}",
                enabled,
            )
        elif kind_id.startswith("channel:"):
            await self._set_tiered(
                user_id,
                tenant_id,
                f"notifications.channels.{kind_id.split(':', 1)[1]}",
                enabled,
            )
        elif kind_id.startswith("integration:"):
            await self._set_integration_key(user_id, kind_id, enabled)
        else:  # pragma: no cover - defensive
            raise NotFoundError(f"Unknown notification kind: {kind_id}")

        return meta

    # ------------------------------------------------------------------
    # State resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_state(
        meta: NotificationKindMeta,
        tiered_values: dict[str, Any],
        user_settings: dict[str, Any],
    ) -> bool:
        """Compute the effective enabled state for one kind."""
        if meta.group == "integration":
            # Per-instance keys live in user JSONB; absent = provider default.
            parts = meta.kind_id.split(":", 2)
            stored = user_settings.get(
                f"notifications.integration.{parts[1]}.{parts[2]}"
            )
            if stored is not None:
                return bool(stored)
            return bool(meta.default_enabled)
        # Source / channel — read from the tiered effective values.
        suffix = meta.kind_id.split(":", 1)[1]
        key = (
            f"notifications.sources.{suffix}"
            if meta.group == "source"
            else f"notifications.channels.{suffix}"
        )
        value = tiered_values.get(key)
        if value is None:
            return True
        return bool(value)

    # ------------------------------------------------------------------
    # Store writers
    # ------------------------------------------------------------------

    async def _set_tiered(
        self,
        user_id: UUID,
        tenant_id: Optional[UUID],
        key: str,
        enabled: bool,
    ) -> None:
        """Mutate a tiered source/channel setting at the USER level.

        Re-enabling removes the override (fall back to tenant/system/default);
        disabling stores an explicit ``False``.
        """
        await SettingsService(self.db).update_override(
            SettingLevel.USER,
            key,
            None if enabled else False,
            user_id,
            tenant_id,
        )

    async def _set_integration_key(
        self, user_id: UUID, kind_id: str, enabled: bool
    ) -> None:
        """Mutate an ad-hoc per-instance key in ``UserModel.settings``.

        Re-enabling removes the override (fall back to provider default);
        disabling stores an explicit ``False``.
        """
        try:
            _, iid, tid = kind_id.split(":", 2)
        except ValueError:
            raise NotFoundError(f"Malformed integration kind id: {kind_id}")
        key = f"notifications.integration.{iid}.{tid}"

        user = await self._load_user_row(user_id)
        settings = dict(user.settings or {})
        if enabled:
            settings.pop(key, None)
        else:
            settings[key] = False
        user.settings = settings
        flag_modified(user, "settings")
        await self.db.commit()

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def _find_kind_for_user(
        self, user_id: UUID, kind_id: str
    ) -> Optional[NotificationKindMeta]:
        """Confirm ``kind_id`` is one of the user's addressable kinds."""
        metas = await enumerate_for_user(self.db, user_id)
        for meta in metas:
            if meta.kind_id == kind_id:
                return meta
        return None

    async def _load_user_settings(self, user_id: UUID) -> dict[str, Any]:
        row = (
            await self.db.execute(
                select(UserModel.settings).where(UserModel.id == user_id)
            )
        ).scalar_one_or_none()
        return dict(row or {})

    async def _load_user_row(self, user_id: UUID) -> UserModel:
        user = (
            await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User not found")
        return user

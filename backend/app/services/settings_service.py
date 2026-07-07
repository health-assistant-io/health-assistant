"""Tiered settings resolution + per-level overrides.

Resolution order for a tiered setting: USER > TENANT > SYSTEM > built-in default.
Device settings are never stored server-side; this service only deals with the
``tiered`` storage scope.
"""

from typing import Any, Dict, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.settings_definitions import (
    SETTINGS_CATEGORIES,
    get_all_definitions,
    get_definition,
    get_tiered_defaults,
)
from app.models.enums import Role
from app.models.system_setting import SystemSetting
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.settings import SettingDefinition, SettingLevel, SettingStorage


class SettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --------------------------------------------------------------
    # Definitions / catalog
    # --------------------------------------------------------------
    @staticmethod
    def get_definitions():
        return get_all_definitions()

    @staticmethod
    def get_categories():
        return list(SETTINGS_CATEGORIES)

    # --------------------------------------------------------------
    # Resolution
    # --------------------------------------------------------------
    async def resolve_effective(
        self, user_id: UUID, tenant_id: UUID
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Return (values, sources) for every tiered setting.

        ``sources[key]`` is one of ``user|tenant|system|default`` so the UI can
        show where an effective value came from.
        """
        user_overrides = await self._load_user_overrides(user_id)
        tenant_overrides = await self._load_tenant_overrides(tenant_id)
        system_overrides = await self._load_system_overrides()

        values: Dict[str, Any] = {}
        sources: Dict[str, str] = {}

        for key, default in get_tiered_defaults().items():
            if key in user_overrides:
                values[key] = user_overrides[key]
                sources[key] = SettingLevel.USER.value
            elif key in tenant_overrides:
                values[key] = tenant_overrides[key]
                sources[key] = SettingLevel.TENANT.value
            elif key in system_overrides:
                values[key] = system_overrides[key]
                sources[key] = SettingLevel.SYSTEM.value
            else:
                values[key] = default
                sources[key] = "default"

        return values, sources

    # --------------------------------------------------------------
    # Per-level reads
    # --------------------------------------------------------------
    async def get_level_overrides(
        self, level: SettingLevel, user_id: UUID, tenant_id: UUID
    ) -> Dict[str, Any]:
        raw = await self._load_raw_overrides(level, user_id, tenant_id)
        allowed = {
            d.key
            for d in get_all_definitions()
            if d.storage == SettingStorage.TIERED and level in d.allowed_levels
        }
        return {k: v for k, v in raw.items() if k in allowed}

    # --------------------------------------------------------------
    # Per-level writes
    # --------------------------------------------------------------
    async def update_override(
        self,
        level: SettingLevel,
        key: str,
        value: Any,
        user_id: UUID,
        tenant_id: UUID,
    ) -> None:
        definition = get_definition(key)
        if definition is None:
            raise ValueError(f"Unknown setting key: {key}")
        if definition.storage != SettingStorage.TIERED:
            raise ValueError(f"Setting '{key}' is not a tiered setting")
        if level not in definition.allowed_levels:
            raise ValueError(f"Setting '{key}' cannot be set at {level.value} level")

        if value is not None:
            value = _coerce_and_validate(definition, value)

        raw = await self._load_raw_overrides(level, user_id, tenant_id)
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value

        await self._persist_raw_overrides(level, raw, user_id, tenant_id)

    # --------------------------------------------------------------
    # Low-level loaders
    # --------------------------------------------------------------
    async def _load_raw_overrides(
        self, level: SettingLevel, user_id: UUID, tenant_id: UUID
    ) -> Dict[str, Any]:
        if level == SettingLevel.USER:
            return await self._load_user_overrides(user_id)
        if level == SettingLevel.TENANT:
            return await self._load_tenant_overrides(tenant_id)
        return await self._load_system_overrides()

    async def _load_user_overrides(self, user_id: UUID) -> Dict[str, Any]:
        if user_id is None:
            return {}
        result = await self.db.execute(
            select(UserModel.settings).where(UserModel.id == user_id)
        )
        settings = result.scalar_one_or_none()
        return dict(settings or {})

    async def _load_tenant_overrides(self, tenant_id: UUID) -> Dict[str, Any]:
        if tenant_id is None:
            return {}
        result = await self.db.execute(
            select(TenantModel.settings).where(TenantModel.id == tenant_id)
        )
        settings = result.scalar_one_or_none()
        return dict(settings or {})

    async def _load_system_overrides(self) -> Dict[str, Any]:
        result = await self.db.execute(select(SystemSetting.key, SystemSetting.value))
        return {row[0]: row[1] for row in result.all()}

    async def _persist_raw_overrides(
        self,
        level: SettingLevel,
        raw: Dict[str, Any],
        user_id: UUID,
        tenant_id: UUID,
    ) -> None:
        if level == SettingLevel.USER:
            await self._persist_user(raw, user_id)
        elif level == SettingLevel.TENANT:
            await self._persist_tenant(raw, tenant_id)
        else:
            await self._persist_system(raw)
        await self.db.commit()

    async def _persist_user(self, raw: Dict[str, Any], user_id: UUID) -> None:
        result = await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError("User not found")
        user.settings = raw
        flag_modified(user, "settings")

    async def _persist_tenant(self, raw: Dict[str, Any], tenant_id: UUID) -> None:
        result = await self.db.execute(
            select(TenantModel).where(TenantModel.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise ValueError("Tenant not found")
        tenant.settings = raw
        flag_modified(tenant, "settings")

    async def _persist_system(self, raw: Dict[str, Any]) -> None:
        result = await self.db.execute(select(SystemSetting))
        existing = {row.key: row for row in result.scalars().all()}
        for key, value in raw.items():
            if key in existing:
                existing[key].value = value
            else:
                self.db.add(SystemSetting(key=key, value=value))
        orphan_keys = set(existing.keys()) - set(raw.keys())
        for key in orphan_keys:
            await self.db.delete(existing[key])


def can_manage_level(
    role: str, level: SettingLevel, tenant_id: UUID, target_tenant_id: UUID
) -> bool:
    """Authorize ``role`` to read/write ``level`` overrides."""
    if role == Role.SYSTEM_ADMIN.value:
        return True
    if level == SettingLevel.USER:
        return True
    if level == SettingLevel.TENANT and role == Role.ADMIN.value:
        return str(tenant_id) == str(target_tenant_id)
    return False


def _coerce_and_validate(definition: SettingDefinition, value: Any) -> Any:
    t = definition.type
    if t.value == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError(f"Setting '{definition.key}' expects a boolean")
    if t.value == "integer":
        try:
            coerced = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Setting '{definition.key}' expects an integer")
        _check_bounds(definition, coerced)
        return coerced
    if t.value == "float":
        try:
            coerced = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Setting '{definition.key}' expects a number")
        _check_bounds(definition, coerced)
        return coerced
    if t.value == "enum":
        allowed = [o.value for o in (definition.options or [])]
        if value not in allowed:
            raise ValueError(f"Setting '{definition.key}' must be one of {allowed}")
        return value
    if isinstance(value, str):
        return value
    raise ValueError(f"Setting '{definition.key}' expects a string")


def _check_bounds(definition: SettingDefinition, value: float) -> None:
    if definition.min is not None and value < definition.min:
        raise ValueError(f"Setting '{definition.key}' must be >= {definition.min}")
    if definition.max is not None and value > definition.max:
        raise ValueError(f"Setting '{definition.key}' must be <= {definition.max}")

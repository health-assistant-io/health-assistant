"""Pydantic schemas for the tiered settings system."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class SettingType(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    STRING = "string"
    ENUM = "enum"


class SettingStorage(StrEnum):
    TIERED = "tiered"
    DEVICE = "device"


class SettingLevel(StrEnum):
    SYSTEM = "system"
    TENANT = "tenant"
    USER = "user"


class SettingCategory(BaseModel):
    key: str
    label_key: str
    description_key: str | None = None
    order: int = 0


class SettingEnumOption(BaseModel):
    value: str
    label_key: str


class SettingDefinition(BaseModel):
    key: str
    category: str
    type: SettingType
    default: Any
    storage: SettingStorage = SettingStorage.TIERED
    allowed_levels: list[SettingLevel]
    label_key: str
    description_key: str
    min: float | None = None
    max: float | None = None
    options: list[SettingEnumOption] | None = None
    order: int = 0


class EffectiveSettingsResponse(BaseModel):
    settings: dict[str, Any]
    sources: dict[str, str]


class SettingsOverrideUpdate(BaseModel):
    key: str
    value: Any = None

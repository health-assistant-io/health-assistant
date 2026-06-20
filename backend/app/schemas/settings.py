"""Pydantic schemas for the tiered settings system."""
from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel


class SettingType(str, Enum):
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    STRING = "string"
    ENUM = "enum"


class SettingStorage(str, Enum):
    TIERED = "tiered"
    DEVICE = "device"


class SettingLevel(str, Enum):
    SYSTEM = "system"
    TENANT = "tenant"
    USER = "user"


class SettingCategory(BaseModel):
    key: str
    label_key: str
    description_key: Optional[str] = None
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
    allowed_levels: List[SettingLevel]
    label_key: str
    description_key: str
    min: Optional[float] = None
    max: Optional[float] = None
    options: Optional[List[SettingEnumOption]] = None
    order: int = 0


class EffectiveSettingsResponse(BaseModel):
    settings: Dict[str, Any]
    sources: Dict[str, str]


class SettingsOverrideUpdate(BaseModel):
    key: str
    value: Any = None

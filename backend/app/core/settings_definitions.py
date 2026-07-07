"""Metadata-driven registry of all platform settings.

Adding a setting = append one entry to SETTINGS_REGISTRY. The API, the
resolution service, and the frontend UI panel are all derived from this list.

Storage model
-------------
* ``tiered`` settings live server-side and resolve USER > TENANT > SYSTEM >
  built-in default. They follow the account across devices and admins can set
  defaults for their scope.
* ``device`` settings are per-browser (localStorage). Use these for prefs that
  legitimately differ per device, e.g. light/dark theme.
"""

from typing import Dict, List, Optional

from app.schemas.settings import (
    SettingCategory,
    SettingDefinition,
    SettingEnumOption,
    SettingLevel,
    SettingStorage,
    SettingType,
)


SETTINGS_CATEGORIES: List[SettingCategory] = [
    SettingCategory(
        key="appearance",
        label_key="settings.category.appearance",
        description_key="settings.category.appearance_desc",
        order=10,
    ),
    SettingCategory(
        key="localization",
        label_key="settings.category.localization",
        description_key="settings.category.localization_desc",
        order=20,
    ),
    SettingCategory(
        key="notifications",
        label_key="settings.category.notifications",
        description_key="settings.category.notifications_desc",
        order=30,
    ),
]


ALL_LEVELS = [SettingLevel.SYSTEM, SettingLevel.TENANT, SettingLevel.USER]


SETTINGS_REGISTRY: List[SettingDefinition] = [
    # ---------------- Appearance / Visualization ----------------
    SettingDefinition(
        key="appearance.biomarker_precision",
        category="appearance",
        type=SettingType.INTEGER,
        default=0,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.biomarker_precision",
        description_key="settings.biomarker_precision_desc",
        min=0,
        max=6,
        order=10,
    ),
    SettingDefinition(
        key="appearance.precision_below_10",
        category="appearance",
        type=SettingType.INTEGER,
        default=1,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.precision_below_10",
        description_key="settings.precision_below_10_desc",
        min=0,
        max=6,
        order=12,
    ),
    SettingDefinition(
        key="appearance.precision_below_30",
        category="appearance",
        type=SettingType.INTEGER,
        default=1,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.precision_below_30",
        description_key="settings.precision_below_30_desc",
        min=0,
        max=6,
        order=11,
    ),
    SettingDefinition(
        key="appearance.precision_below_3",
        category="appearance",
        type=SettingType.INTEGER,
        default=2,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.precision_below_3",
        description_key="settings.precision_below_3_desc",
        min=0,
        max=6,
        order=13,
    ),
    SettingDefinition(
        key="appearance.precision_below_1",
        category="appearance",
        type=SettingType.INTEGER,
        default=3,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.precision_below_1",
        description_key="settings.precision_below_1_desc",
        min=0,
        max=6,
        order=14,
    ),
    SettingDefinition(
        key="appearance.show_reference_ranges",
        category="appearance",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.show_reference_ranges",
        description_key="settings.show_reference_ranges_desc",
        order=20,
    ),
    SettingDefinition(
        key="appearance.show_relative_scores",
        category="appearance",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.show_relative_scores",
        description_key="settings.show_relative_scores_desc",
        order=30,
    ),
    SettingDefinition(
        key="appearance.compact_dashboard",
        category="appearance",
        type=SettingType.BOOLEAN,
        default=False,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.compact_dashboard",
        description_key="settings.compact_dashboard_desc",
        order=40,
    ),
    SettingDefinition(
        key="appearance.date_format",
        category="appearance",
        type=SettingType.ENUM,
        default="YYYY-MM-DD",
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.date_format",
        description_key="settings.date_format_desc",
        options=[
            SettingEnumOption(value="YYYY-MM-DD", label_key="settings.date_format_iso"),
            SettingEnumOption(value="DD/MM/YYYY", label_key="settings.date_format_eu"),
            SettingEnumOption(value="MM/DD/YYYY", label_key="settings.date_format_us"),
        ],
        order=50,
    ),
    SettingDefinition(
        key="appearance.theme",
        category="appearance",
        type=SettingType.ENUM,
        default="light",
        storage=SettingStorage.DEVICE,
        allowed_levels=[SettingLevel.USER],
        label_key="settings.theme",
        description_key="settings.theme_desc",
        options=[
            SettingEnumOption(value="light", label_key="settings.theme_light"),
            SettingEnumOption(value="dark", label_key="settings.theme_dark"),
        ],
        order=5,
    ),
    # ---------------- Localization ----------------
    SettingDefinition(
        key="localization.language",
        category="localization",
        type=SettingType.ENUM,
        default="en",
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.language",
        description_key="settings.language_desc",
        options=[
            SettingEnumOption(value="en", label_key="settings.language_en"),
            SettingEnumOption(value="el", label_key="settings.language_el"),
        ],
        order=10,
    ),
    SettingDefinition(
        key="localization.unit_system",
        category="localization",
        type=SettingType.ENUM,
        default="metric",
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.unit_system",
        description_key="settings.unit_system_desc",
        options=[
            SettingEnumOption(value="metric", label_key="settings.unit_system_metric"),
            SettingEnumOption(
                value="imperial", label_key="settings.unit_system_imperial"
            ),
        ],
        order=20,
    ),
    # ---------------- Notifications ----------------
    SettingDefinition(
        key="notifications.enabled",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.DEVICE,
        allowed_levels=[SettingLevel.USER],
        label_key="settings.notifications_enabled",
        description_key="settings.notifications_enabled_desc",
        order=10,
    ),
    SettingDefinition(
        key="notifications.sources.SYSTEM",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_system",
        description_key="settings.notifications_source_system_desc",
        order=20,
    ),
    SettingDefinition(
        key="notifications.sources.SCHEDULED",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_scheduled",
        description_key="settings.notifications_source_scheduled_desc",
        order=21,
    ),
    SettingDefinition(
        key="notifications.sources.RULE",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_rule",
        description_key="settings.notifications_source_rule_desc",
        order=22,
    ),
    SettingDefinition(
        key="notifications.sources.AGENT",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_agent",
        description_key="settings.notifications_source_agent_desc",
        order=23,
    ),
    SettingDefinition(
        key="notifications.sources.INTEGRATION",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_integration",
        description_key="settings.notifications_source_integration_desc",
        order=24,
    ),
    SettingDefinition(
        key="notifications.sources.CLINICAL",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_source_clinical",
        description_key="settings.notifications_source_clinical_desc",
        order=25,
    ),
    SettingDefinition(
        key="notifications.channels.IN_APP",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_channel_in_app",
        description_key="settings.notifications_channel_in_app_desc",
        order=30,
    ),
    SettingDefinition(
        key="notifications.channels.PUSH",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=True,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_channel_push",
        description_key="settings.notifications_channel_push_desc",
        order=31,
    ),
    SettingDefinition(
        key="notifications.channels.EMAIL",
        category="notifications",
        type=SettingType.BOOLEAN,
        default=False,
        storage=SettingStorage.TIERED,
        allowed_levels=ALL_LEVELS,
        label_key="settings.notifications_channel_email",
        description_key="settings.notifications_channel_email_desc",
        order=32,
    ),
]


_REGISTRY_BY_KEY: Dict[str, SettingDefinition] = {d.key: d for d in SETTINGS_REGISTRY}


def get_all_definitions() -> List[SettingDefinition]:
    return list(SETTINGS_REGISTRY)


def get_definition(key: str) -> Optional[SettingDefinition]:
    return _REGISTRY_BY_KEY.get(key)


def get_tiered_defaults() -> Dict[str, object]:
    return {
        d.key: d.default
        for d in SETTINGS_REGISTRY
        if d.storage == SettingStorage.TIERED
    }


def get_device_defaults() -> Dict[str, object]:
    return {
        d.key: d.default
        for d in SETTINGS_REGISTRY
        if d.storage == SettingStorage.DEVICE
    }

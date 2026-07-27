"""Single source of truth for the "kind" of a notification.

A **kind** is the addressable unit a user can mute. It generalises the
notification ``source`` enum and the per-integration-instance notification
type into one concept. Every kind resolves to:

* ``kind_id``    — canonical id (``"source:INTEGRATION"`` or
                   ``"integration:{integration_id}:{type_id}"`` or
                   ``"channel:PUSH"``)
* ``label``      — human label for the mute button / settings list
* ``group``      — UI grouping: ``"source"`` | ``"integration"`` | ``"channel"``
* ``manage_url`` — deep link to the settings page that controls this kind
* ``mutable``    — ``False`` for safety-critical kinds (mute hidden in UI)
* ``default_enabled`` — provider default (integration kinds only); ``True``
                        for every other kind.

Two entry points:

* :func:`resolve_kind` — derives the kind for a notification being emitted,
  from ``(source, type, source_ref, severity)``. Pure for source/channel
  kinds; best-effort DB lookup for the integration-instance label.

* :func:`enumerate_for_user` — returns every kind relevant to a user (the 6
  source kinds, 3 channel kinds, and one kind per
  ``(integration_instance, declared_type)``). Powers
  ``GET /notifications/preferences``.

Emitters never define kind metadata; the frontend never computes it. This
module is the only place that owns the mapping.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    NotificationChannel,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URLs (kept here so the registry is the single source of truth for both the
# kind id AND the deep-link target).
# ---------------------------------------------------------------------------

SETTINGS_URL = "/notifications/settings"
RULES_URL = "/notifications/rules"
_INTEGRATION_TAB_URL = "/settings/integrations/{iid}?tab=notifications"


# ---------------------------------------------------------------------------
# Source kind metadata (label + canonical manage URL).
# ---------------------------------------------------------------------------

_SOURCE_LABELS: dict[NotificationSource, str] = {
    NotificationSource.SYSTEM: "System notifications",
    NotificationSource.SCHEDULED: "Reminders & scheduled notifications",
    NotificationSource.RULE: "Biomarker rule alerts",
    NotificationSource.AGENT: "AI assistant notifications",
    NotificationSource.INTEGRATION: "Integration notifications",
    NotificationSource.CLINICAL: "Clinical event notifications",
}

# Sources whose default landing page is the biomarker-rules tab (otherwise
# the central settings hub).
_RULE_MANAGED_SOURCES = frozenset({NotificationSource.RULE})

# Channels (orthogonal delivery controls, surfaced only in the settings hub).
_CHANNEL_LABELS: dict[NotificationChannel, str] = {
    NotificationChannel.IN_APP: "In-app notifications",
    NotificationChannel.PUSH: "Push notifications",
    NotificationChannel.EMAIL: "Email notifications",
}

# Channels locked off until their transport lands. Mutability is False so the
# UI hides the toggle / mute button — users cannot enable them yet.
_LOCKED_CHANNELS = frozenset({NotificationChannel.EMAIL})

# Safety-critical combinations: never mutable. The mute button is hidden but
# the manage link is still offered (so the user can see what's happening).
_IMMUTABLE_TYPES = frozenset({NotificationType.SYSTEM_ERROR})
_IMMUTABLE_CRITICAL_SOURCES = frozenset(
    {NotificationSource.SYSTEM, NotificationSource.CLINICAL}
)


@dataclass(frozen=True)
class NotificationKindMeta:
    """The addressable metadata for one notification kind."""

    kind_id: str
    label: str
    group: str
    manage_url: str
    mutable: bool
    default_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Mutability
# ---------------------------------------------------------------------------


def _is_source_mutable(
    source: NotificationSource,
    type_: Optional[NotificationType],
    severity: Optional[NotificationSeverity],
) -> bool:
    """Return False for safety-critical notifications that must never be muted."""
    if type_ is not None and type_ in _IMMUTABLE_TYPES:
        return False
    if (
        severity == NotificationSeverity.CRITICAL
        and source in _IMMUTABLE_CRITICAL_SOURCES
    ):
        return False
    return True


def _source_manage_url(source: NotificationSource) -> str:
    return RULES_URL if source in _RULE_MANAGED_SOURCES else SETTINGS_URL


# ---------------------------------------------------------------------------
# Integration-instance label resolution (best-effort DB lookup)
# ---------------------------------------------------------------------------


async def _integration_instance_label(
    session: AsyncSession, integration_id: str, provider: Optional[str]
) -> str:
    """Resolve a human label for the integration kind.

    Falls back to the provider domain when the row can't be loaded or has no
    ``instance_name`` set — the mute button still renders with *some* label.
    """
    fallback = provider or "integration"
    try:
        from app.models.user_integration import UserIntegration

        row = (
            await session.execute(
                select(UserIntegration.instance_name).where(
                    UserIntegration.id == UUID(integration_id)
                )
            )
        ).scalar_one_or_none()
        if row:
            return row
    except (ValueError, Exception) as exc:  # noqa: BLE001
        # ValueError: bad UUID string. Anything else: DB / driver issue.
        logger.debug(
            "Could not resolve integration instance label for %s: %s",
            integration_id,
            exc,
        )
    return fallback


# ---------------------------------------------------------------------------
# resolve_kind — called from emit()
# ---------------------------------------------------------------------------


async def resolve_kind(
    session: AsyncSession,
    source: NotificationSource,
    type_: NotificationType,
    source_ref: Optional[dict[str, Any]],
    severity: NotificationSeverity = NotificationSeverity.INFO,
) -> Optional[NotificationKindMeta]:
    """Derive the notification kind for an emission.

    Returns ``None`` when no kind can be derived (the frontend then shows no
    inline mute button — graceful degradation). Never raises.
    """
    sr = source_ref or {}

    # Integration per-instance kind: needs both integration_id + type_id in
    # source_ref. Set by ``_emit_provider_notifications`` for provider-authored
    # notifications whose spec carried a ``type_id``.
    if source == NotificationSource.INTEGRATION:
        iid = sr.get("integration_id")
        tid = sr.get("type_id")
        if iid and tid:
            label = await _integration_instance_label(session, str(iid), sr.get("provider"))
            return NotificationKindMeta(
                kind_id=f"integration:{iid}:{tid}",
                label=label,
                group="integration",
                manage_url=_INTEGRATION_TAB_URL.format(iid=iid),
                mutable=True,
            )
        # No type_id → platform-level integration notification (sync outcome /
        # sync failure). Falls through to the source-level INTEGRATION kind.

    # Source-level kind (also the fallback for un-typed integration emissions).
    if source in _SOURCE_LABELS:
        return NotificationKindMeta(
            kind_id=f"source:{source.value}",
            label=_SOURCE_LABELS[source],
            group="source",
            manage_url=_source_manage_url(source),
            mutable=_is_source_mutable(source, type_, severity),
        )

    return None


# ---------------------------------------------------------------------------
# enumerate_for_user — called from the preferences endpoint / service
# ---------------------------------------------------------------------------


async def enumerate_for_user(
    session: AsyncSession, user_id: UUID
) -> list[NotificationKindMeta]:
    """Every kind addressable by ``user_id`` (sources + channels + integrations).

    Sources + channels are static. Integration kinds are dynamic — one per
    ``(integration_instance, declared_type)`` for each of the user's enabled
    integrations whose provider declares notification types.
    """
    out: list[NotificationKindMeta] = []

    # Sources
    for source in NotificationSource:
        out.append(
            NotificationKindMeta(
                kind_id=f"source:{source.value}",
                label=_SOURCE_LABELS[source],
                group="source",
                manage_url=_source_manage_url(source),
                mutable=_is_source_mutable(source, None, None),
            )
        )

    # Channels (only those with a registered tiered setting — SMS has no
    # transport and no settings key, so it's never surfaced as manageable).
    for channel, label in _CHANNEL_LABELS.items():
        out.append(
            NotificationKindMeta(
                kind_id=f"channel:{channel.value}",
                label=label,
                group="channel",
                manage_url=SETTINGS_URL,
                mutable=channel not in _LOCKED_CHANNELS,
            )
        )

    # Integration kinds (dynamic).
    out.extend(await _enumerate_integration_kinds(session, user_id))
    return out


async def _enumerate_integration_kinds(
    session: AsyncSession, user_id: UUID
) -> list[NotificationKindMeta]:
    """One kind per (integration_instance, declared notification type)."""
    try:
        from app.core.integration_registry import integration_registry
        from app.models.user_integration import UserIntegration
    except Exception:  # pragma: no cover - import guard
        return []

    try:
        integrations = (
            await session.execute(
                select(UserIntegration).where(UserIntegration.user_id == user_id)
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to enumerate integration kinds for %s: %s", user_id, exc)
        return []

    out: list[NotificationKindMeta] = []
    for integration in integrations:
        provider = integration_registry.get_provider(integration.provider)
        if provider is None:
            continue
        getter = getattr(provider, "get_notification_types", None)
        if getter is None:
            continue
        try:
            types = getter() or []
        except Exception:
            logger.exception(
                "get_notification_types failed for %s", integration.provider
            )
            continue
        if not types:
            continue

        iid = str(integration.id)
        instance_name = integration.instance_name or integration.provider
        manage_url = _INTEGRATION_TAB_URL.format(iid=iid)
        for t in types:
            out.append(
                NotificationKindMeta(
                    kind_id=f"integration:{iid}:{t.id}",
                    label=f"{t.label} — {instance_name}",
                    group="integration",
                    manage_url=manage_url,
                    mutable=True,
                    default_enabled=getattr(t, "default_enabled", True),
                )
            )
    return out


__all__ = [
    "NotificationKindMeta",
    "resolve_kind",
    "enumerate_for_user",
    "SETTINGS_URL",
    "RULES_URL",
]

"""Notification authoring helpers for integration providers.

Providers opt into rich, event-driven notifications by:

1. Overriding :meth:`BaseHealthProvider.supports_notifications` to return ``True``.
2. Implementing :meth:`BaseHealthProvider.get_notifications` to inspect the
   just-synced observations and return a list of :class:`NotificationSpec`
   objects.
3. (Optional) Implementing :meth:`BaseHealthProvider.handle_notification_action`
   to respond to clicked action buttons.

The platform calls these hooks from ``integration_sync_service.run_sync``
after observations are persisted. Specs are converted to ``emit()`` calls
with the integration owner as the default target (mirroring the legacy
sync-outcome behavior).

This module mirrors :mod:`integrations.sdk.observation_builder` (a fluent
builder over a Pydantic model) and :mod:`integrations.sdk.display` (small
builder helpers that document the payload contract in one place).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

# Re-use the display-layer action_result (same shape: {message, results, ...extra}).
# Documented here so integration authors have one-stop import coverage.
from .display import action_result  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Action button spec
# ---------------------------------------------------------------------------


VALID_ACTION_STYLES = {"primary", "danger", "ghost", "default"}
VALID_ACTION_TYPES = {"link", "post"}


@dataclass
class NotificationAction:
    """A button rendered inside the notification detail modal.

    The shape mirrors the frontend ``NotificationAction`` TypeScript type
    exactly so the round-trip is identity. ``type="link"`` navigates to
    ``url`` (relative URL → react-router; absolute → new tab). ``type="post"``
    POSTs to ``endpoint`` via the platform
    ``POST /integrations/{domain}/notification-action/{iid}/{action_id}``
    route, which dispatches to the provider's ``handle_notification_action``.
    """

    id: str
    label: str
    type: str = "link"
    url: Optional[str] = None
    endpoint: Optional[str] = None
    method: str = "POST"
    style: str = "default"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "label": self.label, "type": self.type}
        if self.url is not None:
            d["url"] = self.url
        if self.endpoint is not None:
            d["endpoint"] = self.endpoint
            d["method"] = self.method
        d["style"] = self.style
        return d

    def __post_init__(self) -> None:
        if self.type not in VALID_ACTION_TYPES:
            raise ValueError(
                f"NotificationAction.type must be one of {VALID_ACTION_TYPES}, got {self.type!r}"
            )
        if self.style not in VALID_ACTION_STYLES:
            raise ValueError(
                f"NotificationAction.style must be one of {VALID_ACTION_STYLES}, got {self.style!r}"
            )
        if self.type == "link" and not self.url:
            raise ValueError("NotificationAction with type='link' requires a url")
        if self.type == "post" and not self.endpoint:
            raise ValueError("NotificationAction with type='post' requires an endpoint")


# ---------------------------------------------------------------------------
# Notification spec
# ---------------------------------------------------------------------------


@dataclass
class NotificationSpec:
    """A provider-authored notification ready for ``emit()``.

    Build with :class:`NotificationSpecBuilder`:

    ::

        spec = (
            NotificationSpec.builder(
                title="Elevated heart rate",
                body="120 bpm observed",
                category=NotificationCategory.ALERT,
                severity=NotificationSeverity.WARNING,
            )
            .type_id("elevated_heart_rate")   # link to a declared NotificationTypeSpec
            .patient_id(integration.patient_id)
            .add_link_actions("View trend", f"/patients/{pid}/biomarkers/heart_rate")
            .add_post_action(
                "Acknowledge",
                endpoint=f"/integrations/{domain}/notification-action/{iid}",
                style="ghost",
            )
            .display_block(table_block(...))
            .build()
        )
    """

    title: str
    body: Optional[str] = None
    category: str = "integration"  # NotificationCategory.value
    severity: str = "info"  # NotificationSeverity.value
    type: str = "INTEGRATION_EVENT"  # NotificationType.value
    type_id: Optional[str] = None  # links to a NotificationTypeSpec.id
    patient_id: Optional[UUID | str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    source_ref: dict[str, Any] = field(default_factory=dict)
    actions: list[NotificationAction] = field(default_factory=list)
    display_blocks: list[dict[str, Any]] = field(default_factory=list)
    # If set, overrides the default "integration owner only" target.
    targets_override: Optional[list[dict[str, Any]]] = None

    @staticmethod
    def builder(
        *,
        title: str,
        body: Optional[str] = None,
        category: str = "integration",
        severity: str = "info",
        type: str = "INTEGRATION_EVENT",
    ) -> "NotificationSpecBuilder":
        return NotificationSpecBuilder(
            title=title, body=body, category=category, severity=severity, type=type
        )

    def to_payload(self) -> dict[str, Any]:
        """Build the ``payload`` dict that gets stored on the Notification row."""
        payload: dict[str, Any] = dict(self.payload)
        if self.actions:
            payload["actions"] = [a.to_dict() for a in self.actions]
        if self.display_blocks:
            payload["display_blocks"] = self.display_blocks
        return payload


# ---------------------------------------------------------------------------
# Fluent builder
# ---------------------------------------------------------------------------


class NotificationSpecBuilder:
    """Fluent builder for :class:`NotificationSpec`."""

    def __init__(
        self,
        *,
        title: str,
        body: Optional[str],
        category: str,
        severity: str,
        type: str,
    ) -> None:
        self._spec = NotificationSpec(
            title=title, body=body, category=category, severity=severity, type=type
        )

    def body_text(self, body: str) -> "NotificationSpecBuilder":
        self._spec.body = body
        return self

    def type_id(self, type_id: str) -> "NotificationSpecBuilder":
        """Link this spec to a declared ``NotificationTypeSpec.id``.

        Linked specs are filtered by the user's per-integration-type
        preferences (``user.settings["notifications.integration.{domain}.
        {type_id}"]``). Specs without a ``type_id`` always pass through.
        """
        self._spec.type_id = type_id
        return self

    def patient_id(self, pid: UUID | str | None) -> "NotificationSpecBuilder":
        self._spec.patient_id = pid
        return self

    def payload_field(self, key: str, value: Any) -> "NotificationSpecBuilder":
        """Add an arbitrary key/value to the notification ``payload``."""
        self._spec.payload[key] = value
        return self

    def source_ref(self, key: str, value: Any) -> "NotificationSpecBuilder":
        """Add an arbitrary key/value to the notification ``source_ref``."""
        self._spec.source_ref[key] = value
        return self

    def add_action(self, action: NotificationAction) -> "NotificationSpecBuilder":
        self._spec.actions.append(action)
        return self

    def add_link_action(
        self,
        label: str,
        url: str,
        *,
        id: Optional[str] = None,
        style: str = "primary",
    ) -> "NotificationSpecBuilder":
        """Shortcut for the most common action: navigate to an app URL."""
        self._spec.actions.append(
            NotificationAction(
                id=id or label.lower().replace(" ", "_"),
                label=label,
                type="link",
                url=url,
                style=style,
            )
        )
        return self

    def add_post_action(
        self,
        label: str,
        *,
        endpoint: str,
        id: Optional[str] = None,
        method: str = "POST",
        style: str = "default",
    ) -> "NotificationSpecBuilder":
        """Add a server-side action button.

        ``endpoint`` should be a path under ``/integrations/...``. The
        platform routes POST clicks to ``handle_notification_action``.
        """
        self._spec.actions.append(
            NotificationAction(
                id=id or label.lower().replace(" ", "_"),
                label=label,
                type="post",
                endpoint=endpoint,
                method=method,
                style=style,
            )
        )
        return self

    def display_block(self, block: dict[str, Any]) -> "NotificationSpecBuilder":
        """Attach a DisplayBlock (kv/list/table/json/text/code) to the payload.

        The notification detail modal renders ``payload.display_blocks[]``
        using the same renderer as ``ActionResultModal``.
        """
        self._spec.display_blocks.append(block)
        return self

    def targets(self, targets: list[dict[str, Any]]) -> "NotificationSpecBuilder":
        """Override the default "integration owner only" target."""
        self._spec.targets_override = targets
        return self

    def build(self) -> NotificationSpec:
        return self._spec


# ---------------------------------------------------------------------------
# Result of handle_notification_action — re-uses display.action_result
# (same shape: {message, results=[DisplayBlock...], **extra}). Authors can
# import it from either module; we re-export here for one-stop shopping.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Static type declaration — what a provider says it CAN emit (opt-in)
# ---------------------------------------------------------------------------


@dataclass
class NotificationTypeSpec:
    """Static declaration of a notification kind a provider can emit.

    Providers override :meth:`BaseHealthProvider.get_notification_types`
    to return a list of these. The platform aggregates them into:

    * A new "Notifications" tab on each ``IntegrationDetail`` page
      (conditional — only rendered when at least one type is declared)
    * A new "Per-integration" collapsible section in
      ``/settings/notifications`` that rolls up every enabled integration's
      types in one place

    Users toggle per-type (stored at
    ``user.settings["notifications.integration.{domain}.{id}"]``).
    ``NotificationSpec.type_id`` links a runtime emission to a declared
    type — linked specs are filtered by the user's pref; unlinked specs
    always pass through (backwards-compatible).

    Default to ``default_enabled=True`` — users opt OUT, not IN. Otherwise
    nobody discovers the feature.
    """

    id: str
    label: str
    description: str
    category: str = "integration"  # default category for specs tagged with this id
    severity: str = "info"  # default severity
    default_enabled: bool = True
    channels: tuple = ("IN_APP", "PUSH")  # suggested channels (advisory)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "default_enabled": self.default_enabled,
            "channels": list(self.channels),
        }


__all__ = [
    "NotificationAction",
    "NotificationSpec",
    "NotificationSpecBuilder",
    "NotificationTypeSpec",
    "action_result",
    "VALID_ACTION_STYLES",
    "VALID_ACTION_TYPES",
]

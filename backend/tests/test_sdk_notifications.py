"""Unit tests for integrations.sdk.notifications dataclasses.

Coverage today is minimal — the dev_dummy reference implementation exercises
the behavior end-to-end via the integration endpoints, but there was no
unit-level guard that the ``NotificationSpec`` defaults are stable. This file
pins the defaults so future edits can't silently regress them.
"""
from uuid import uuid4

from integrations.sdk.notifications import (
    NotificationAction,
    NotificationSpec,
    NotificationTypeSpec,
)


# ---------------------------------------------------------------------------
# NotificationSpec defaults
# ---------------------------------------------------------------------------


def test_notification_spec_defaults_are_stable():
    """Catches a regression on the ``source_ref`` field — it was previously
    declared with a dead ``field(default_factory=list) if False else
    field(default_factory=dict)`` branch. The list branch was dead code; the
    field has always been a dict. This test pins the contract."""
    spec = NotificationSpec(title="Heart rate elevated")
    assert spec.title == "Heart rate elevated"
    assert spec.body is None
    assert spec.category == "integration"
    assert spec.severity == "info"
    assert spec.type == "INTEGRATION_EVENT"
    assert spec.type_id is None
    assert spec.patient_id is None
    # The regression guard:
    assert spec.source_ref == {}, (
        "source_ref must default to an empty dict — the prior dead-branch "
        "declaration `field(default_factory=list) if False else "
        "field(default_factory=dict)` was effectively a dict but invited "
        "a future revert to a list."
    )
    assert spec.actions == []
    assert spec.display_blocks == []
    assert spec.targets_override is None


def test_notification_spec_to_payload_serializes_actions_and_blocks():
    action = NotificationAction(
        id="open",
        label="Open",
        type="link",
        url="https://example.com",
    )
    spec = NotificationSpec(
        title="x",
        actions=[action],
        display_blocks=[{"type": "kv", "title": "t", "items": {}}],
        payload={"foo": "bar"},
    )
    payload = spec.to_payload()
    assert payload["foo"] == "bar"
    assert payload["actions"] == [action.to_dict()]
    assert payload["display_blocks"] == [
        {"type": "kv", "title": "t", "items": {}}
    ]


# ---------------------------------------------------------------------------
# NotificationSpecBuilder
# ---------------------------------------------------------------------------


def test_builder_produces_equivalent_spec():
    pid = uuid4()
    spec = (
        NotificationSpec.builder(title="Threshold breached")
        .body_text("Heart rate 130 bpm")
        .type_id("threshold-heart-rate")
        .patient_id(pid)
        .payload_field("value", 130)
        .add_link_action(id="open", label="Open", url="https://app/heart")
        .build()
    )
    assert spec.title == "Threshold breached"
    assert spec.body == "Heart rate 130 bpm"
    assert spec.type_id == "threshold-heart-rate"
    assert spec.patient_id == pid
    assert spec.payload["value"] == 130
    assert len(spec.actions) == 1
    assert spec.actions[0].type == "link"
    assert spec.actions[0].url == "https://app/heart"


# ---------------------------------------------------------------------------
# NotificationTypeSpec
# ---------------------------------------------------------------------------


def test_type_spec_defaults_and_to_dict():
    t = NotificationTypeSpec(
        id="threshold-heart-rate",
        label="Heart-rate threshold",
        description="Fires when heart rate crosses the configured band",
    )
    assert t.category == "integration"
    assert t.severity == "info"
    assert t.default_enabled is True
    assert t.channels == ("IN_APP", "PUSH")

    d = t.to_dict()
    assert d["id"] == "threshold-heart-rate"
    assert d["default_enabled"] is True
    assert d["channels"] == ["IN_APP", "PUSH"]

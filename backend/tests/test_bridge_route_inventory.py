"""Frozen inventory of the bridge's served API surface.

``HealthAssistantBridgeProvider._ROUTES`` is the single source of truth for
what ``handle_api_request`` dispatches. These tests pin the exact
``(METHOD, pattern)`` set — adding or removing a route without updating the
frozen list fails CI — and verify every entry resolves to a handler with the
uniform route-handler signature ``(integration, request, **named-groups)``.
"""

from inspect import signature

import pytest

from app.extensions.builtin.health_assistant_bridge.provider import (
    _ROUTES,
    HealthAssistantBridgeProvider,
)

FROZEN_ROUTES: set[tuple[str, str]] = {
    ("DELETE", "^ai/sessions/(?P<session_id>[^/]+)$"),
    ("DELETE", "^allergies/(?P<allergy_id>[^/]+)$"),
    ("DELETE", "^clinical-events/(?P<event_id>[^/]+)$"),
    ("DELETE", "^doctors/(?P<doctor_id>[^/]+)$"),
    ("DELETE", "^documents/(?P<doc_id>[^/]+)$"),
    ("DELETE", "^examinations/(?P<exam_id>[^/]+)$"),
    ("DELETE", "^medications/(?P<medication_id>[^/]+)$"),
    ("DELETE", "^notifications/register-device/(?P<device_id>[^/]+)$"),
    ("DELETE", "^notifications/triggers/(?P<trigger_id>[^/]+)$"),
    ("DELETE", "^vaccines/(?P<vaccine_id>[^/]+)$"),
    ("GET", "^ai/sessions/(?P<session_id>[^/]+)/messages$"),
    ("GET", "^clinical-events/(?P<event_id>[^/]+)$"),
    ("GET", "^documents/(?P<doc_id>[^/]+)$"),
    ("GET", "^documents/(?P<doc_id>[^/]+)/content$"),
    ("GET", "^documents/(?P<doc_id>[^/]+)/extract/status$"),
    ("GET", "^documents/(?P<doc_id>[^/]+)/preview$"),
    ("GET", "^documents/(?P<doc_id>[^/]+)/text$"),
    ("GET", "^examinations/(?P<exam_id>[^/]+)$"),
    ("GET", "^examinations/(?P<exam_id>[^/]+)/documents$"),
    ("GET", "^examinations/(?P<exam_id>[^/]+)/logs$"),
    ("GET", "^examinations/(?P<exam_id>[^/]+)/status$"),
    ("GET", "ai/sessions"),
    ("GET", "allergies"),
    ("GET", "biomarkers"),
    ("GET", "changes"),
    ("GET", "clinical-events"),
    ("GET", "devices"),
    ("GET", "doctors"),
    ("GET", "documents"),
    ("GET", "examinations"),
    ("GET", "medications"),
    ("GET", "notifications/inbox"),
    ("GET", "notifications/preferences"),
    ("GET", "notifications/triggers"),
    ("GET", "notifications/unread-count"),
    ("GET", "observations"),
    ("GET", "observations/latest"),
    ("GET", "status"),
    ("GET", "vaccines"),
    ("PATCH", "^ai/sessions/(?P<session_id>[^/]+)$"),
    ("PATCH", "^notifications/(?P<recipient_id>[^/]+)/(?P<action>read|dismiss)$"),
    ("POST", "^ai/sessions/(?P<session_id>[^/]+)/resume$"),
    ("POST", "^ai/sessions/(?P<session_id>[^/]+)/tasks/(?P<proposal_id>[^/]+)/resolve$"),
    ("POST", "^clinical-events/(?P<event_id>[^/]+)/occurrences$"),
    ("POST", "^documents/(?P<doc_id>[^/]+)/extract$"),
    ("POST", "^examinations/(?P<exam_id>[^/]+)/documents$"),
    ("POST", "ai/chat"),
    ("POST", "ai/chat/stream"),
    ("POST", "ai/transcribe"),
    ("POST", "allergies"),
    ("POST", "clinical-events"),
    ("POST", "doctors"),
    ("POST", "examinations"),
    ("POST", "map"),
    ("POST", "medications"),
    ("POST", "notifications/read-all"),
    ("POST", "notifications/register-device"),
    ("POST", "notifications/triggers"),
    ("POST", "sync"),
    ("POST", "vaccines"),
    ("PUT", "^allergies/(?P<allergy_id>[^/]+)$"),
    ("PUT", "^clinical-events/(?P<event_id>[^/]+)$"),
    ("PUT", "^doctors/(?P<doctor_id>[^/]+)$"),
    ("PUT", "^medications/(?P<medication_id>[^/]+)$"),
    ("PUT", "^notifications/preferences/(?P<kind_id>[^/]+)$"),
    ("PUT", "^vaccines/(?P<vaccine_id>[^/]+)$"),
}


def _served_routes() -> set[tuple[str, str]]:
    return {
        (method, pattern if isinstance(pattern, str) else pattern.pattern)
        for method, pattern, _handler in _ROUTES
    }


def test_route_inventory_is_frozen():
    """The served (METHOD, pattern) set must match this file exactly."""
    assert _served_routes() == FROZEN_ROUTES


def test_route_entries_are_unique():
    """No duplicate (METHOD, pattern) rows hidden by set comparison."""
    assert len(_ROUTES) == len(_served_routes())


@pytest.mark.parametrize(
    ("method", "pattern", "handler_name"),
    _ROUTES,
    ids=[f"{m}-{p if isinstance(p, str) else p.pattern}" for m, p, _ in _ROUTES],
)
def test_route_handler_matches_pattern(method, pattern, handler_name):
    """Each route resolves to a handler whose signature covers its groups."""
    handler = getattr(HealthAssistantBridgeProvider, handler_name, None)
    assert callable(handler), f"missing handler {handler_name!r} for {method} {pattern}"
    params = set(signature(handler).parameters) - {"self"}
    expected = {"integration", "request"}
    if not isinstance(pattern, str):
        assert pattern.pattern.startswith("^") and pattern.pattern.endswith("$")
        expected |= set(pattern.groupindex)
    assert params == expected, f"{handler_name} params {params} != {expected}"

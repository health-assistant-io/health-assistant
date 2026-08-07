"""Source-level guard: the bridge provider routes observations through the
shared ``apply_telemetry_split`` helper rather than inlining a stale copy.

Regression guard for the bug where ``_process_and_save_sync_data``
constructed ``TelemetryDataModel(heart_rate=, steps=, calories=, data=)``
— kwargs removed in the long-format hypertable rewrite (migration
``t1e2l3o4n5g6``). The model now takes ``slug=, value=, unit=, patient_id=``,
so the inlined copy raised ``TypeError`` on any wearable record (silently
swallowed into a ``failed`` sync log). The behavioural correctness of the
split helper itself is covered by ``test_integration_sync_telemetry_split.py``;
these tests pin the wiring (bridge → shared helper, no inline construction).
"""
import ast
import inspect

import integrations.health_assistant_bridge.provider as bridge_module
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider


def _tree() -> ast.Module:
    return ast.parse(inspect.getsource(bridge_module))


def _calls_to(name: str) -> list[ast.Call]:
    """Return every ``name(...)`` call expression anywhere in the module."""
    out: list[ast.Call] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                out.append(node)
    return out


def test_bridge_imports_apply_telemetry_split():
    """The shared helper is imported and called in the sync path."""
    src = inspect.getsource(bridge_module)
    assert "from app.services.integration_sync_service import apply_telemetry_split", (
        "bridge must import the shared apply_telemetry_split helper"
    )
    assert _calls_to("apply_telemetry_split"), (
        "bridge must call apply_telemetry_split in its sync path"
    )


def test_bridge_does_not_construct_telemetry_model_directly():
    """The bridge must not construct ``TelemetryDataModel(...)`` itself — the
    shared ``apply_telemetry_split`` helper owns the long-format row shape
    (``slug=, value=, unit=, patient_id=``). Pre-fix the bridge inlined
    ``TelemetryDataModel(heart_rate=, steps=, calories=, data=)`` using kwargs
    removed in migration ``t1e2l3o4n5g6`` → ``TypeError`` on every wearable
    record. AST inspection (not substring) so explanatory comments referencing
    the old pattern aren't false positives.
    """
    constructions = _calls_to("TelemetryDataModel")
    assert not constructions, (
        f"bridge must not construct TelemetryDataModel directly "
        f"(found {len(constructions)} call(s)); route through "
        f"apply_telemetry_split which owns the row shape"
    )


def test_bridge_provider_loads_without_dead_hmac_symbols():
    """The redundant provider-side HMAC path (``_require_hmac`` /
    ``_get_api_secret`` / ``_HMAC_PROTECTED_PATHS``) is gone — the platform
    endpoint is the single auth chokepoint."""
    assert not hasattr(HealthAssistantBridgeProvider, "_require_hmac")
    assert not hasattr(HealthAssistantBridgeProvider, "_get_api_secret")
    src = inspect.getsource(bridge_module)
    assert "_HMAC_PROTECTED_PATHS" not in src
    # No direct import of the verifier — endpoint handles auth.
    tree = _tree()
    imported_names = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "verify_canonical_signature" not in imported_names


def test_bridge_handle_api_request_dispatches_three_paths():
    """The public three-endpoint surface (status/map/sync) is intact."""
    assert hasattr(HealthAssistantBridgeProvider, "handle_api_request")
    assert inspect.iscoroutinefunction(HealthAssistantBridgeProvider.handle_api_request)


"""Source-level guard: the bridge routes observations through the one ingest
funnel rather than inlining a stale split copy.

Regression guard for the bug where ``_process_and_save_sync_data``
constructed ``TelemetryDataModel(heart_rate=, steps=, calories=, data=)``
— kwargs removed in the long-format hypertable rewrite (migration
``0001_baseline``). The funnel (``services/integration_ingest.py``) owns the
row shape, dedup and mapping-gap recording for every inbound path; these
tests pin the wiring (bridge → funnel, no inline construction).
"""

import ast
import inspect

import app.extensions.builtin.health_assistant_bridge.provider as bridge_module
from app.extensions.builtin.health_assistant_bridge.provider import HealthAssistantBridgeProvider


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


def test_bridge_routes_observations_through_the_ingest_funnel():
    """The one inbound path is imported and called in the sync path."""
    source = inspect.getsource(bridge_module)
    assert (
        "from app.services.integration.integration_ingest import ingest_observations" in source
    ), "bridge must import the ingest funnel"
    assert _calls_to("ingest_observations"), "bridge must call ingest_observations in its sync path"
    assert "apply_telemetry_split" not in source, (
        "the bridge must not carry its own split path — the funnel owns it"
    )


def test_bridge_does_not_construct_telemetry_model_directly():
    """The bridge must not construct ``TelemetryDataModel(...)`` itself — the
    ingest funnel owns the long-format row shape (``slug=, value=, unit=,
    patient_id=``). Pre-fix the bridge inlined ``TelemetryDataModel(heart_rate=,
    steps=, calories=, data=)`` using kwargs removed by the long-format
    baseline → ``TypeError`` on every wearable record. AST inspection (not
    substring) so explanatory comments aren't false positives.
    """
    constructions = _calls_to("TelemetryDataModel")
    assert not constructions, (
        f"bridge must not construct TelemetryDataModel directly "
        f"(found {len(constructions)} call(s)); route through "
        f"the ingest funnel which owns the row shape"
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
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "verify_canonical_signature" not in imported_names


def test_bridge_handle_api_request_dispatches_three_paths():
    """The public three-endpoint surface (status/map/sync) is intact."""
    assert hasattr(HealthAssistantBridgeProvider, "handle_api_request")
    assert inspect.iscoroutinefunction(HealthAssistantBridgeProvider.handle_api_request)

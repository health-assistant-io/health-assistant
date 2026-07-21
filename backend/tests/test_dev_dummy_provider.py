"""Unit tests for the dev_dummy reference integration.

These tests instantiate :class:`DevDummyProvider` and
:class:`DevDummyConfigFlow` directly with lightweight fake
``UserIntegration`` objects (no DB needed). They exercise every SDK
capability the integration demonstrates so the reference stays correct
as the SDK evolves.

Coverage map (each test pins one capability):

* ``test_pull_data_emits_quantitative_and_categorical``     — §A, §E
* ``test_pull_data_raises_auth_error``                       — §B
* ``test_pull_data_raises_rate_limit_error``                 — §B
* ``test_pull_data_advances_cursor``                         — §C
* ``test_pull_data_sensor_glitch_can_trigger_high_outlier``  — §B (sensor)
* ``test_get_schema_includes_instance_name_and_secret``      — config flow
* ``test_max_instances_per_user_set``                        — config flow
* ``test_get_secret_fields_declares_webhook_secret``         — secret lifecycle
* ``test_webhook_builds_observations_without_secret``        — §H (no HMAC)
* ``test_webhook_rejects_invalid_signature``                 — §H (HMAC)
* ``test_webhook_accepts_valid_signature``                   — §H (HMAC)
* ``test_api_request_status_responds``                       — §I
* ``test_api_request_echo_round_trip``                       — §I
* ``test_api_request_unknown_path_raises``                   — §I
* ``test_supports_tools_returns_langchain_tools``            — §J
* ``test_pull_clinical_events_emits_one_event``              — §K
* ``test_pull_clinical_events_respects_toggle``              — §K
* ``test_pull_examinations_emits_one_exam``                  — §L
* ``test_pull_catalog_proposals_emits_biomarker``            — §M
* ``test_pull_hitl_proposals_emits_concept``                 — §N
* ``test_pull_hitl_proposals_skips_after_resolution``        — §N (cursor)
* ``test_handle_proposal_resolution_advances_cursor``        — §N
* ``test_pull_documents_delivers_once``                      — §O
* ``test_pull_documents_skips_after_delivery``               — §O
* ``test_latest_numeric_picks_most_recent``                  — bugfix guard
* ``test_notifications_fire_for_elevated_hr``                — §G
* ``test_notifications_fire_for_sensor_malfunction``         — §G + §B (glitch)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict
from uuid import uuid4

import pytest

from integrations.dev_dummy.config_flow import DevDummyConfigFlow
from integrations.dev_dummy.provider import DevDummyProvider
from integrations.sdk.exceptions import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationRateLimitError,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_integration(
    *,
    config: Dict[str, Any] | None = None,
    patient_id: Any = None,
    debug: bool = False,
    instance_name: str = "test-instance",
) -> SimpleNamespace:
    """Build a minimal stand-in for ``UserIntegration``.

    The provider only touches a handful of attributes — we don't need
    SQLAlchemy or a DB session to exercise its hooks.
    """
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        patient_id=patient_id or uuid4(),
        provider="dev_dummy",
        instance_name=instance_name,
        is_debug_enabled=debug,
        user_config=dict(config or {}),
    )


@pytest.fixture
def provider() -> DevDummyProvider:
    return DevDummyProvider()


@pytest.fixture
def flow() -> DevDummyConfigFlow:
    return DevDummyConfigFlow()


class FakeRequest:
    """Minimal stand-in for ``fastapi.Request`` used by webhook tests."""

    def __init__(self, body_bytes: bytes = b"", headers: Dict[str, str] | None = None):
        self._body = body_bytes
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body

    async def json(self) -> Any:
        import json

        return json.loads(self._body or b"{}")


# ---------------------------------------------------------------------------
# §A / §B / §C / §E — pull_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_data_emits_quantitative_and_categorical(provider: DevDummyProvider):
    integration = make_integration(
        config={
            "generate_heart_rate": True,
            "generate_blood_pressure": True,
            "generate_weight": True,
            "generate_mood": True,
        }
    )
    obs = await provider.pull_data(integration)

    codes = {c.get("code") for o in obs for c in o.code.get("coding", [])}
    assert "8867-4" in codes, "heart rate (quantitative) missing"
    assert "8480-6" in codes, "systolic (quantitative) missing"
    assert "8462-4" in codes, "diastolic (quantitative) missing"
    assert "29463-7" in codes, "body weight (quantitative) missing"
    assert "dev-dummy-mood" in codes, "mood (categorical) missing"

    # Quantitative observation carries value_quantity; categorical carries value_string.
    mood = next(o for o in obs if any(c.get("code") == "dev-dummy-mood" for c in o.code["coding"]))
    assert mood.value_string in {"good", "ok", "bad"}, mood.value_string
    assert mood.value_quantity is None, "categorical must not set value_quantity"
    assert mood.raw_value is None, "categorical must leave raw_value unset"

    hr = next(o for o in obs if any(c.get("code") == "8867-4" for c in o.code["coding"]))
    assert hr.value_quantity is not None and hr.value_quantity["value"] >= 60
    assert hr.value_string is None, "quantitative must not set value_string"


@pytest.mark.asyncio
async def test_pull_data_raises_auth_error(provider: DevDummyProvider):
    integration = make_integration(config={"simulate_auth_error": True})
    with pytest.raises(IntegrationAuthError):
        await provider.pull_data(integration)


@pytest.mark.asyncio
async def test_pull_data_raises_rate_limit_error(provider: DevDummyProvider):
    integration = make_integration(config={"simulate_rate_limit": True})
    with pytest.raises(IntegrationRateLimitError):
        await provider.pull_data(integration)


@pytest.mark.asyncio
async def test_pull_data_advances_cursor(provider: DevDummyProvider):
    """Cursor write/read round-trip — the provider must mutate
    ``user_config['_sync_state']`` so SQLAlchemy's JSONB mutation
    detection picks it up."""
    integration = make_integration(config={})
    original_cursor = provider.get_sync_cursor(integration, "last_timestamp")
    assert original_cursor is None

    await provider.pull_data(integration)

    new_cursor = provider.get_sync_cursor(integration, "last_timestamp")
    assert isinstance(new_cursor, str), "cursor must be an ISO string after pull_data"
    # The cursor is a tz-aware ISO timestamp; parsing must succeed.
    parsed = datetime.fromisoformat(new_cursor)
    assert parsed.tzinfo is not None, "cursor must be timezone-aware"


@pytest.mark.asyncio
async def test_pull_data_sensor_glitch_can_trigger_high_outlier(provider: DevDummyProvider):
    """When ``simulate_sensor_glitch`` is on, the random HR occasionally
    exceeds 200. Run enough iterations that at least one sync produces
    a reading > 200 so the critical-notification path is reachable.
    """
    integration = make_integration(config={"simulate_sensor_glitch": True})
    saw_outlier = False
    for _ in range(200):
        obs = await provider.pull_data(integration)
        for o in obs:
            if any(c.get("code") == "8867-4" for c in o.code.get("coding", [])):
                if o.raw_value is not None and o.raw_value > 200:
                    saw_outlier = True
                    break
        if saw_outlier:
            break
    assert saw_outlier, "sensor glitch never produced an out-of-range reading"


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schema_includes_instance_name_and_secret(flow: DevDummyConfigFlow):
    schema = await flow.get_schema()
    props = schema["data_schema"]["properties"]
    assert "instance_name" in props, "instance_name must be in the schema (multi-instance)"
    assert "webhook_secret" in props, "webhook_secret field missing"
    assert "sync_interval" in props
    # Every opt-in toggle we ship in the provider must be configurable.
    for key in (
        "enable_clinical_events",
        "enable_examinations",
        "enable_catalog_proposals",
        "enable_hitl_proposals",
        "enable_documents",
        "enable_tools",
        "simulate_sensor_glitch",
        "generate_mood",
    ):
        assert key in props, f"missing schema property: {key}"


@pytest.mark.asyncio
async def test_validate_input_rejects_bad_sync_interval(flow: DevDummyConfigFlow):
    with pytest.raises(ValueError):
        await flow.validate_input({"sync_interval": -1, "instance_name": "x"})
    with pytest.raises(ValueError):
        await flow.validate_input({"sync_interval": "nope", "instance_name": "x"})


@pytest.mark.asyncio
async def test_validate_input_rejects_missing_instance_name(flow: DevDummyConfigFlow):
    with pytest.raises(ValueError):
        await flow.validate_input({"sync_interval": 15, "instance_name": ""})


@pytest.mark.asyncio
async def test_validate_input_accepts_valid(flow: DevDummyConfigFlow):
    out = await flow.validate_input({"sync_interval": 5, "instance_name": "Demo"})
    assert out["sync_interval"] == 5


def test_max_instances_per_user_set(flow: DevDummyConfigFlow):
    """Per-user cap is enforced by the endpoint on create."""
    assert flow.max_instances_per_user == 3


def test_get_secret_fields_declares_webhook_secret(flow: DevDummyConfigFlow):
    assert flow.get_secret_fields() == ["webhook_secret"]


# ---------------------------------------------------------------------------
# §H — Webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_builds_observations_without_secret(provider: DevDummyProvider):
    integration = make_integration(config={})  # no webhook_secret configured
    payload = {
        "metrics": [
            {"code": "8867-4", "value": 72, "unit": "bpm"},
            {"code": "94500-6", "value_string": "POSITIVE"},
        ]
    }
    request = FakeRequest(body_bytes=b'{"metrics": []}')
    obs = await provider.handle_webhook(integration, payload, request=request)
    codes = {c.get("code") for o in obs for c in o.code.get("coding", [])}
    assert "8867-4" in codes
    assert "94500-6" in codes


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature_when_secret_set(
    provider: DevDummyProvider, monkeypatch
):
    """When ``webhook_secret`` is configured, the request MUST carry
    ``X-DevDummy-Signature``. Bypass the Fernet decrypt path by
    monkeypatching the resolver to return the plaintext secret."""
    integration = make_integration(config={"webhook_secret": "topsecret"})
    monkeypatch.setattr(
        DevDummyProvider, "_resolve_webhook_secret", staticmethod(lambda i: "topsecret")
    )
    request = FakeRequest(body_bytes=b"{}", headers={})
    with pytest.raises(IntegrationDataError):
        await provider.handle_webhook(integration, {"metrics": []}, request=request)


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(provider: DevDummyProvider, monkeypatch):
    integration = make_integration(config={"webhook_secret": "topsecret"})
    monkeypatch.setattr(
        DevDummyProvider, "_resolve_webhook_secret", staticmethod(lambda i: "topsecret")
    )
    request = FakeRequest(
        body_bytes=b'{"metrics":[]}',
        headers={"X-DevDummy-Signature": "deadbeef"},
    )
    with pytest.raises(IntegrationDataError):
        await provider.handle_webhook(integration, {"metrics": []}, request=request)


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature(provider: DevDummyProvider, monkeypatch):
    secret = "topsecret"
    integration = make_integration(config={"webhook_secret": secret})
    monkeypatch.setattr(
        DevDummyProvider, "_resolve_webhook_secret", staticmethod(lambda i: secret)
    )
    body = b'{"metrics":[{"code":"8867-4","value":80,"unit":"bpm"}]}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = FakeRequest(body_bytes=body, headers={"X-DevDummy-Signature": sig})
    obs = await provider.handle_webhook(
        integration, __import__("json").loads(body), request=request
    )
    assert len(obs) == 1
    assert obs[0].raw_value == 80.0


@pytest.mark.asyncio
async def test_webhook_rejects_non_dict_payload(provider: DevDummyProvider):
    integration = make_integration(config={})
    with pytest.raises(IntegrationDataError):
        await provider.handle_webhook(integration, "not-a-dict", request=FakeRequest())


# ---------------------------------------------------------------------------
# §I — Two-way API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_request_status_responds(provider: DevDummyProvider):
    integration = make_integration(config={"generate_heart_rate": True})
    resp = await provider.handle_api_request(integration, "status", "GET", request=None)
    assert resp["domain"] == "dev_dummy"
    assert resp["instance_name"] == "test-instance"
    assert "pull_data: heart_rate" in resp["capabilities"]


@pytest.mark.asyncio
async def test_api_request_cursor_responds(provider: DevDummyProvider):
    integration = make_integration(config={})
    resp = await provider.handle_api_request(integration, "cursor", "GET", request=None)
    assert "cursor" in resp


@pytest.mark.asyncio
async def test_api_request_reset_clears_cursor(provider: DevDummyProvider):
    integration = make_integration(config={})
    provider.set_sync_cursor(integration, "last_timestamp", "2099-01-01T00:00:00+00:00")
    resp = await provider.handle_api_request(integration, "reset", "POST", request=None)
    assert resp == {"ok": True, "cursor": None}
    assert provider.get_sync_cursor(integration, "last_timestamp") is None


@pytest.mark.asyncio
async def test_api_request_echo_round_trip(provider: DevDummyProvider):
    integration = make_integration(config={})
    body = b'{"hello":"world"}'
    resp = await provider.handle_api_request(
        integration, "echo", "POST", request=FakeRequest(body_bytes=body)
    )
    assert resp["you_sent"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_api_request_unknown_path_raises(provider: DevDummyProvider):
    integration = make_integration(config={})
    with pytest.raises(NotImplementedError):
        await provider.handle_api_request(integration, "nope", "GET", request=None)


# ---------------------------------------------------------------------------
# §J — Chat tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supports_tools_returns_langchain_tools(provider: DevDummyProvider):
    try:
        from langchain_core.tools import StructuredTool  # noqa: F401
    except ImportError:
        pytest.skip("langchain-core not installed")
    integration = make_integration(config={"enable_tools": True})
    tools = await provider.get_tools(integration)
    assert len(tools) == 2, "dev_dummy should expose two demo tools"
    assert all(hasattr(t, "coroutine") or asyncio.iscoroutinefunction(getattr(t, "func", None)) for t in tools)


@pytest.mark.asyncio
async def test_supports_tools_returns_empty_when_disabled(provider: DevDummyProvider):
    integration = make_integration(config={"enable_tools": False})
    tools = await provider.get_tools(integration)
    assert tools == []


def test_supports_tools_flag(provider: DevDummyProvider):
    assert provider.supports_tools() is True


# ---------------------------------------------------------------------------
# §K — Clinical events
# ---------------------------------------------------------------------------


def test_supports_clinical_events_flag(provider: DevDummyProvider):
    assert provider.supports_clinical_events() is True


@pytest.mark.asyncio
async def test_pull_clinical_events_emits_one_event(provider: DevDummyProvider):
    integration = make_integration(config={"enable_clinical_events": True})
    events = await provider.pull_clinical_events(integration)
    assert len(events) == 1
    event = events[0]
    assert event.external_id == "dev_dummy_flu_episode_demo"
    assert event.patient_id == integration.patient_id
    assert event.onset_date is not None


@pytest.mark.asyncio
async def test_pull_clinical_events_respects_toggle(provider: DevDummyProvider):
    integration = make_integration(config={"enable_clinical_events": False})
    assert await provider.pull_clinical_events(integration) == []


# ---------------------------------------------------------------------------
# §L — Examinations
# ---------------------------------------------------------------------------


def test_supports_examinations_flag(provider: DevDummyProvider):
    assert provider.supports_examinations() is True


@pytest.mark.asyncio
async def test_pull_examinations_emits_one_exam(provider: DevDummyProvider):
    integration = make_integration(config={"enable_examinations": True})
    exams = await provider.pull_examinations(integration)
    assert len(exams) == 1
    exam = exams[0]
    assert exam.external_id == "dev_dummy_annual_checkup_demo"
    assert exam.patient_id == integration.patient_id
    assert exam.examination_date is not None


@pytest.mark.asyncio
async def test_pull_examinations_respects_toggle(provider: DevDummyProvider):
    integration = make_integration(config={"enable_examinations": False})
    assert await provider.pull_examinations(integration) == []


# ---------------------------------------------------------------------------
# §M — Catalog proposals (auto-apply)
# ---------------------------------------------------------------------------


def test_supports_catalog_proposals_flag(provider: DevDummyProvider):
    assert provider.supports_catalog_proposals() is True


@pytest.mark.asyncio
async def test_pull_catalog_proposals_emits_biomarker(provider: DevDummyProvider):
    integration = make_integration(config={"enable_catalog_proposals": True})
    proposals = await provider.pull_catalog_proposals(integration)
    assert len(proposals) == 1
    assert proposals[0].kind == "biomarker"
    assert proposals[0].payload["slug"] == "dev_dummy_sleep_quality_score"


@pytest.mark.asyncio
async def test_pull_catalog_proposals_respects_toggle(provider: DevDummyProvider):
    integration = make_integration(config={"enable_catalog_proposals": False})
    assert await provider.pull_catalog_proposals(integration) == []


# ---------------------------------------------------------------------------
# §N — HITL proposals
# ---------------------------------------------------------------------------


def test_supports_hitl_proposals_flag(provider: DevDummyProvider):
    assert provider.supports_hitl_proposals() is True


@pytest.mark.asyncio
async def test_pull_hitl_proposals_emits_concept(provider: DevDummyProvider):
    integration = make_integration(config={"enable_hitl_proposals": True})
    specs = await provider.pull_hitl_proposals(integration)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.proposal_type == "create_concept"
    assert spec.proposed_payload["slug"] == "dev_dummy_stress_index"
    assert spec.context["source"] == "dev_dummy"


@pytest.mark.asyncio
async def test_pull_hitl_proposals_skips_after_resolution(provider: DevDummyProvider):
    integration = make_integration(config={"enable_hitl_proposals": True})
    provider.set_sync_cursor(integration, "hitl_stress_index_resolved", True)
    assert await provider.pull_hitl_proposals(integration) == []


@pytest.mark.asyncio
async def test_pull_hitl_proposals_respects_toggle(provider: DevDummyProvider):
    integration = make_integration(config={"enable_hitl_proposals": False})
    assert await provider.pull_hitl_proposals(integration) == []


@pytest.mark.asyncio
async def test_handle_proposal_resolution_advances_cursor(provider: DevDummyProvider):
    integration = make_integration(config={})
    from integrations.sdk import ProposalOutcome

    await provider.handle_proposal_resolution(integration, uuid4(), ProposalOutcome())
    assert provider.get_sync_cursor(integration, "hitl_stress_index_resolved") is True
    assert provider.get_sync_cursor(integration, "hitl_resolved_count") == 1


# ---------------------------------------------------------------------------
# §O — Documents
# ---------------------------------------------------------------------------


def test_supports_documents_flag(provider: DevDummyProvider):
    assert provider.supports_documents() is True


@pytest.mark.asyncio
async def test_pull_documents_delivers_once(provider: DevDummyProvider):
    integration = make_integration(config={"enable_documents": True})
    docs = await provider.pull_documents(integration)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.filename == "dev_dummy_demo_report.txt"
    assert doc.examination_external_id == "dev_dummy_annual_checkup_demo"
    assert b"DEV DUMMY DEMO LAB REPORT" in doc.content


@pytest.mark.asyncio
async def test_pull_documents_skips_after_delivery(provider: DevDummyProvider):
    integration = make_integration(config={"enable_documents": True})
    await provider.pull_documents(integration)  # first call delivers
    docs = await provider.pull_documents(integration)  # second call must be idempotent
    assert docs == [], "second pull_documents call must not redeliver"


@pytest.mark.asyncio
async def test_pull_documents_respects_toggle(provider: DevDummyProvider):
    integration = make_integration(config={"enable_documents": False})
    assert await provider.pull_documents(integration) == []


# ---------------------------------------------------------------------------
# §G — Notifications
# ---------------------------------------------------------------------------


def _build_obs(
    *,
    code: str = "8867-4",
    raw_value: float | None = 80.0,
    value_string: str | None = None,
    effective: datetime | None = None,
):
    """Build a minimal duck-typed stand-in for ObservationCreate."""
    return SimpleNamespace(
        code={"coding": [{"code": code, "system": "http://loinc.org", "display": "X"}], "text": "X"},
        value_quantity={"value": raw_value} if raw_value is not None else None,
        value_string=value_string,
        raw_value=raw_value,
        effective_datetime=effective or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_notifications_fire_for_elevated_hr(provider: DevDummyProvider):
    integration = make_integration(config={})
    observations = [_build_obs(code="8867-4", raw_value=130.0)]
    specs = await provider.get_notifications(
        integration, observations=observations, context={"sync_result": None}
    )
    types = {s.type_id for s in specs}
    assert "elevated_heart_rate" in types, "elevated HR notification must fire"


@pytest.mark.asyncio
async def test_notifications_fire_for_sensor_malfunction(provider: DevDummyProvider):
    """Guards the previously-dead sensor_malfunction branch — now
    reachable because the glitch toggle can produce HR > 200."""
    integration = make_integration(config={"simulate_sensor_glitch": True})
    observations = [_build_obs(code="8867-4", raw_value=240.0)]
    specs = await provider.get_notifications(
        integration, observations=observations, context={"sync_result": None}
    )
    types = {s.type_id for s in specs}
    assert "sensor_malfunction" in types
    assert "elevated_heart_rate" in types  # also implied by HR > 200


# ---------------------------------------------------------------------------
# Bugfix guard — _latest_numeric
# ---------------------------------------------------------------------------


def test_latest_numeric_picks_most_recent(provider: DevDummyProvider):
    """Regression: the previous implementation parsed effective_datetime
    as a string (would TypeError under the bare except), so the helper
    returned the FIRST match instead of the LATEST."""
    earlier = _build_obs(raw_value=70.0, effective=datetime(2025, 1, 1, tzinfo=timezone.utc))
    later = _build_obs(raw_value=95.0, effective=datetime(2025, 1, 2, tzinfo=timezone.utc))
    # Insert in chronological order so the naive first-match would also
    # return 70.0; the fixed helper must return 95.0.
    result = DevDummyProvider._latest_numeric([earlier, later], "8867-4")
    assert result == 95.0


def test_latest_numeric_ignores_categorical(provider: DevDummyProvider):
    """Categorical observations have ``raw_value=None`` and must be
    skipped (not crash)."""
    categorical = _build_obs(code="dev-dummy-mood", raw_value=None, value_string="good")
    assert DevDummyProvider._latest_numeric([categorical], "dev-dummy-mood") is None


# ---------------------------------------------------------------------------
# §F — Custom actions
# ---------------------------------------------------------------------------


def test_get_custom_actions_lists_three(provider: DevDummyProvider):
    ids = {a["id"] for a in provider.get_custom_actions()}
    assert ids == {"reset_cursor", "show_status", "clear_errors"}


@pytest.mark.asyncio
async def test_custom_action_reset_cursor(provider: DevDummyProvider):
    integration = make_integration(config={})
    provider.set_sync_cursor(integration, "last_timestamp", "2099-01-01T00:00:00+00:00")
    out = await provider.execute_custom_action(integration, "reset_cursor")
    assert "cursor reset" in out["message"].lower()
    assert provider.get_sync_cursor(integration, "last_timestamp") is None


@pytest.mark.asyncio
async def test_custom_action_show_status(provider: DevDummyProvider):
    integration = make_integration(config={"generate_mood": True})
    out = await provider.execute_custom_action(integration, "show_status")
    assert out["message"]
    blocks = {b["title"] for b in out["results"]}
    assert "Sync state" in blocks
    assert "Active capabilities" in blocks


@pytest.mark.asyncio
async def test_custom_action_clear_errors(provider: DevDummyProvider):
    integration = make_integration(config={})
    out = await provider.execute_custom_action(integration, "clear_errors")
    assert "cleared" in out["message"].lower()


@pytest.mark.asyncio
async def test_custom_action_unknown_raises(provider: DevDummyProvider):
    integration = make_integration(config={})
    with pytest.raises(NotImplementedError):
        await provider.execute_custom_action(integration, "nope")

"""Webhook hardening: per-parser schema validation and typed rejections.

Pins the plan-8 W4 contract:

* hostile payloads (oversized, deeply nested, wrong types, non-object roots)
  are rejected with the charter ``protocol`` code — never parsed into an empty
  result;
* a bad parser configuration is ``invalid``;
* the webhook route persists a typed failure row and answers ``4xx`` with
  ``{code, message}`` instead of swallowing the failure.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.extensions.builtin.webhook.parsers import BasicParser, LifeDashboardParser, get_parser
from app.extensions.builtin.webhook.parsers.custom import CustomJSONPathParser
from app.extensions.builtin.webhook.provider import WebhookProvider
from app.extensions.builtin.webhook.validation import (
    MAX_DEPTH,
    MAX_PAYLOAD_BYTES,
    WebhookConfigError,
    WebhookPayloadError,
    validate_payload,
)
from app.extensions.errors import ExtensionError
from app.extensions.sdk.observation_builder import ObservationBuilder


def _builder():
    return ObservationBuilder(tenant_id=uuid.uuid4(), patient_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Structural bounds
# ---------------------------------------------------------------------------


def test_non_object_root_is_a_typed_protocol_rejection():
    with pytest.raises(WebhookPayloadError) as exc:
        validate_payload([{"steps": []}], parser_id="test")
    assert exc.value.code == "protocol"


def test_oversized_payload_is_rejected():
    payload = {"steps": [{"count": 1, "note": "x" * (MAX_PAYLOAD_BYTES + 10)}]}
    with pytest.raises(WebhookPayloadError) as exc:
        validate_payload(payload, parser_id="test")
    assert "budget" in exc.value.detail


def test_deeply_nested_payload_is_rejected():
    payload: dict = {}
    node = payload
    for _ in range(MAX_DEPTH + 5):
        child: dict = {}
        node["nested"] = child
        node = child
    with pytest.raises(WebhookPayloadError) as exc:
        validate_payload(payload, parser_id="test")
    assert "nesting" in exc.value.detail


# ---------------------------------------------------------------------------
# Per-parser schemas
# ---------------------------------------------------------------------------


def test_basic_parser_rejects_wrong_value_type():
    with pytest.raises(WebhookPayloadError):
        BasicParser().parse({"type": "heart_rate", "value": "not-a-number"}, {}, _builder())


def test_basic_parser_rejects_unknown_metric():
    with pytest.raises(WebhookPayloadError):
        BasicParser().parse({"type": "blood_moon", "value": 1}, {}, _builder())


def test_basic_parser_accepts_a_valid_payload():
    observations = BasicParser().parse(
        {"type": "heart_rate", "value": 72, "timestamp": "1700000000000"}, {}, _builder()
    )
    assert len(observations) == 1


def test_life_dashboard_rejects_non_list_section():
    with pytest.raises(WebhookPayloadError):
        LifeDashboardParser().parse(
            {"source": "health_connect", "steps": {"count": 1}}, {}, _builder()
        )


def test_life_dashboard_rejects_wrong_section_field_type():
    with pytest.raises(WebhookPayloadError):
        LifeDashboardParser().parse(
            {"source": "health_connect", "heart_rate": [{"bpm": "fast"}]}, {}, _builder()
        )


def test_life_dashboard_rejects_unknown_source():
    with pytest.raises(WebhookPayloadError):
        LifeDashboardParser().parse({"source": "mystery"}, {}, _builder())


def test_life_dashboard_accepts_a_valid_payload():
    observations = LifeDashboardParser().parse(
        {
            "source": "health_connect",
            "heart_rate": [{"bpm": 68, "time": "2026-10-20T08:00:00Z"}],
        },
        {},
        _builder(),
    )
    assert len(observations) == 1


def test_custom_parser_rejects_invalid_mapping_json():
    with pytest.raises(WebhookConfigError):
        CustomJSONPathParser().parse({}, {"custom_mapping_json": "{not json"}, _builder())


def test_custom_parser_rejects_non_object_mapping():
    with pytest.raises(WebhookConfigError):
        CustomJSONPathParser().parse({}, {"custom_mapping_json": "[]"}, _builder())


# ---------------------------------------------------------------------------
# Provider surface: no silent [] on failure
# ---------------------------------------------------------------------------


class _FakeIntegration:
    def __init__(self, config):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.patient_id = uuid.uuid4()
        self.provider = "webhook"
        self.instance_name = "webhook-test"
        self.user_config = config
        self.is_debug_enabled = False


@pytest.mark.asyncio
async def test_provider_propagates_typed_parser_failure():
    provider = WebhookProvider()
    integration = _FakeIntegration({"parser_type": "basic"})
    with pytest.raises(WebhookPayloadError):
        await provider.handle_webhook(integration, {"type": "heart_rate", "value": "bad"})


@pytest.mark.asyncio
async def test_unknown_parser_is_a_typed_config_error():
    provider = WebhookProvider()
    integration = _FakeIntegration({"parser_type": "does_not_exist"})
    with pytest.raises(WebhookConfigError) as exc:
        await provider.handle_webhook(integration, {"type": "heart_rate", "value": 1})
    assert exc.value.code == "invalid"


@pytest.mark.asyncio
async def test_parser_crash_becomes_a_typed_io_error():
    provider = WebhookProvider()
    integration = _FakeIntegration({"parser_type": "basic"})

    class _Boom(BasicParser):
        def parse(self, payload, config, builder):
            raise RuntimeError("boom")

    with (
        patch(
            "app.extensions.builtin.webhook.provider.get_parser",
            return_value=_Boom(),
        ),
        pytest.raises(ExtensionError) as exc,
    ):
        await provider.handle_webhook(integration, {"type": "heart_rate", "value": 1})
    assert exc.value.code == "io"


def test_get_parser_rejects_unknown_ids():
    with pytest.raises(WebhookConfigError):
        get_parser("nope")


# ---------------------------------------------------------------------------
# Route: typed rejection + failure row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_route_writes_a_typed_failure_row_and_answers_422(async_client):
    from app.core.database import get_db
    from app.main import app
    from app.models.enums import IntegrationStatus

    secret = "route-secret"
    integration_id = uuid.uuid4()

    class FakeIntegration:
        def __init__(self):
            self.id = integration_id
            self.provider = "webhook"
            self.status = IntegrationStatus.ACTIVE
            self.tenant_id = uuid.uuid4()
            self.instance_name = "webhook-test"
            self.patient_id = uuid.uuid4()
            self.user_config = {"webhook_secret": secret, "parser_type": "basic"}
            self.last_synced_at = None
            self.is_debug_enabled = False

    fake_int = FakeIntegration()

    class _Result:
        def scalar_one_or_none(self):
            return fake_int

        def scalars(self):
            return self

        def all(self):
            return []

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=_Result())
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.rollback = AsyncMock()
    fake_db.flush = AsyncMock()

    fake_provider = MagicMock()
    fake_provider.handle_webhook = AsyncMock(
        side_effect=WebhookPayloadError("basic: 'value' must be a number.")
    )

    async def _override_get_db():
        yield fake_db

    body = b'{"type":"heart_rate","value":"not-a-number"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with patch(
            "app.api.v1.endpoints.integrations.integration_registry.get_provider",
            return_value=fake_provider,
        ):
            response = await async_client.post(
                f"/api/v1/integrations/webhook/webhook/{integration_id}",
                content=body,
                headers={
                    "X-Webhook-Signature": signature,
                    "Content-Type": "application/json",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["detail"]["code"] == "protocol"

    added = [call.args[0] for call in fake_db.add.call_args_list]
    logs = [obj for obj in added if type(obj).__name__ == "IntegrationSyncLog"]
    assert logs, "a typed rejection must persist a failure row"
    assert logs[-1].status == "failed"
    assert logs[-1].error_class is not None


@pytest.mark.asyncio
async def test_webhook_route_rejects_malformed_json_typed(async_client):
    from app.core.database import get_db
    from app.main import app
    from app.models.enums import IntegrationStatus

    secret = "route-secret-2"
    integration_id = uuid.uuid4()

    class FakeIntegration:
        def __init__(self):
            self.id = integration_id
            self.provider = "webhook"
            self.status = IntegrationStatus.ACTIVE
            self.tenant_id = uuid.uuid4()
            self.instance_name = "webhook-test"
            self.patient_id = uuid.uuid4()
            self.user_config = {"webhook_secret": secret}
            self.last_synced_at = None
            self.is_debug_enabled = False

    fake_int = FakeIntegration()

    class _Result:
        def scalar_one_or_none(self):
            return fake_int

        def scalars(self):
            return self

        def all(self):
            return []

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=_Result())
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.rollback = AsyncMock()
    fake_db.flush = AsyncMock()

    async def _override_get_db():
        yield fake_db

    body = b"{not-json"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with patch(
            "app.api.v1.endpoints.integrations.integration_registry.get_provider",
            return_value=MagicMock(),
        ):
            response = await async_client.post(
                f"/api/v1/integrations/webhook/webhook/{integration_id}",
                content=body,
                headers={
                    "X-Webhook-Signature": signature,
                    "Content-Type": "application/json",
                },
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "protocol"
    added = [call.args[0] for call in fake_db.add.call_args_list]
    logs = [obj for obj in added if type(obj).__name__ == "IntegrationSyncLog"]
    assert logs and logs[-1].error_class.value == "protocol"

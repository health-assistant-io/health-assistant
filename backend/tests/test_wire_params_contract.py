"""Shared wire-parameter fixture wall (ADR-0038) — health's side.

Loads the vendored fixture table (byte-identical across the family:
``dev/contracts/wire-params.fixtures.json`` → ``tests/fixtures/``). A row
change is a contract change — every repo must pass the same rows. Policy
rows hit the vendored ``app.ai.wire_params`` module directly; temperature
rows run through health's own factory (omit-unless-configured).
"""

import json
from pathlib import Path

import pytest

from app.ai.chat_models import build_openai
from app.ai.wire_params import is_openai_endpoint, resolve_chat_transport, resolve_max_tokens_param

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "wire_params.fixtures.json").read_text()
)


def _temperature_model(row):
    return build_openai(
        api_key="sk-test",
        base_url="http://localhost:1234/v1",
        model_name=FIXTURES["temperatureModelId"],
        temperature=row["temperature"],
        max_tokens=512,
    )


@pytest.mark.parametrize("row", FIXTURES["endpointDetection"], ids=lambda r: r["name"])
def test_endpoint_detection(row):
    assert is_openai_endpoint(row["apiBase"]) is row["expected"]


@pytest.mark.parametrize("row", FIXTURES["maxTokensParam"], ids=lambda r: r["name"])
def test_max_tokens_param_resolution(row):
    assert (
        resolve_max_tokens_param(row["providerType"], row["apiBase"], row["override"])
        == row["expected"]
    )


@pytest.mark.parametrize("row", FIXTURES["chatTransport"], ids=lambda r: r["name"])
def test_chat_transport_resolution(row):
    assert (
        resolve_chat_transport(row["providerType"], row["apiBase"], row["override"])
        == row["expected"]
    )


@pytest.mark.parametrize("row", FIXTURES["temperatureOmission"], ids=lambda r: r["name"])
def test_temperature_omission(row):
    model = _temperature_model(row)
    assert ("temperature" in model._default_params) is row["sent"]

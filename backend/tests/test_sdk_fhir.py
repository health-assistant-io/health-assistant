"""Unit tests for integrations.sdk.fhir (Stage 2 Pair B)."""
from uuid import uuid4

import httpx
import pytest

from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError
from integrations.sdk.fhir import (
    fhir_conditional_update,
    fhir_observation_to_create,
    fhir_search,
    parse_operation_outcome,
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _lab_obs(code="2345-7", value=95, last_updated="2026-06-01T10:31:00Z"):
    return {
        "resourceType": "Observation",
        "id": "obs-1",
        "status": "final",
        "code": {
            "coding": [{"system": "http://loinc.org", "code": code, "display": "Glucose"}],
            "text": "Glucose",
        },
        "subject": {"reference": "Patient/REMOTE-999"},
        "effectiveDateTime": "2026-06-01T10:30:00Z",
        "valueQuantity": {"value": value, "unit": "mg/dL", "code": "mg/dL"},
        "interpretation": [{"coding": [{"code": "N", "display": "Normal"}], "text": "Normal"}],
        "referenceRange": [{"low": {"value": 70}, "high": {"value": 99}}],
        "meta": {"lastUpdated": last_updated},
    }


# ---------- fhir_observation_to_create ----------

def test_observation_to_create_maps_fields_and_localizes_subject():
    local_patient = uuid4()
    created = fhir_observation_to_create(_lab_obs(), tenant_id=uuid4(), patient_id=local_patient)
    assert created is not None
    assert created.code["coding"][0]["code"] == "2345-7"
    assert created.value_quantity["value"] == 95
    # subject is rewritten to the LOCAL patient, not the remote one
    assert created.subject == {"reference": f"Patient/{local_patient}"}
    assert "REMOTE-999" not in created.subject["reference"]
    assert created.interpretation == "Normal"
    # H6: referenceRange is now preserved as the canonical FHIR list (was flattened to {min, max})
    assert created.reference_range == [{"low": {"value": 70}, "high": {"value": 99}}]


def test_observation_to_create_none_without_code():
    assert fhir_observation_to_create(
        {"resourceType": "Observation", "valueQuantity": {"value": 1}},
        tenant_id=uuid4(), patient_id=uuid4(),
    ) is None


def test_observation_to_create_none_without_value():
    assert fhir_observation_to_create(
        {"resourceType": "Observation", "code": {"text": "X"}},
        tenant_id=uuid4(), patient_id=uuid4(),
    ) is None


def test_observation_to_create_accepts_value_string():
    created = fhir_observation_to_create(
        {"resourceType": "Observation", "status": "final", "code": {"text": "X"}, "valueString": "Positive"},
        tenant_id=uuid4(), patient_id=uuid4(),
    )
    assert created is not None
    assert created.value_string == "Positive"


def test_observation_to_create_preserves_category_list():
    # FHIR category is 0..* (a list of CodeableConcept). The SDK must keep the
    # canonical list shape from the source FHIR server (not flatten to a dict),
    # so data stays FHIR-compatible through pull -> store -> push round-trips.
    created = fhir_observation_to_create(
        {
            "resourceType": "Observation", "status": "final",
            "code": {"text": "X"},
            "valueQuantity": {"value": 1, "unit": "u"},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
        },
        tenant_id=uuid4(), patient_id=uuid4(),
    )
    assert created is not None
    assert isinstance(created.category, list)
    assert created.category[0]["coding"][0]["code"] == "laboratory"


def test_observation_to_create_category_none_when_absent():
    created = fhir_observation_to_create(
        {"resourceType": "Observation", "status": "final",
         "code": {"text": "X"}, "valueQuantity": {"value": 1, "unit": "u"}},
        tenant_id=uuid4(), patient_id=uuid4(),
    )
    assert created is not None
    assert created.category is None


# ---------- parse_operation_outcome ----------

def test_parse_operation_outcome_diagnostics():
    oo = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "diagnostics": "boom"}]}
    assert "boom" in parse_operation_outcome(oo)


def test_parse_operation_outcome_fallback():
    assert isinstance(parse_operation_outcome({"text": {"div": "nope"}}), str)
    assert isinstance(parse_operation_outcome("plain string"), str)


# ---------- fhir_search ----------

@pytest.mark.asyncio
async def test_fhir_search_returns_flat_resource_list():
    bundle = {
        "resourceType": "Bundle", "type": "searchset",
        "entry": [{"resource": _lab_obs("1")}, {"resource": _lab_obs("2")}],
    }
    async with _client(lambda r: httpx.Response(200, json=bundle)) as http:
        results = await fhir_search(http, "https://ehr/fhir", "Observation", {"patient": "REMOTE-999"})
    assert len(results) == 2


@pytest.mark.asyncio
async def test_fhir_search_tokenless_passes_no_bearer():
    """Tokenless mode (local HAPI) sends no Authorization header."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    async with _client(handler) as http:
        await fhir_search(http, "https://ehr/fhir", "Observation", {}, access_token=None)
    assert seen["auth"] is None  # no Bearer header for tokenless servers


@pytest.mark.asyncio
async def test_fhir_search_with_token_sends_bearer():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    async with _client(handler) as http:
        await fhir_search(http, "https://ehr/fhir", "Observation", {}, access_token="TOK")
    assert seen["auth"] == "Bearer TOK"


# ---------- fhir_conditional_update ----------


def _obs_body(local_id="abc"):
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7"}]},
        "subject": {"reference": "Patient/REMOTE-1"},
        "identifier": [{"system": "urn:healthassistant:observation", "value": local_id}],
    }


@pytest.mark.asyncio
async def test_conditional_update_create_returns_201_and_resource():
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = request.read().decode()
        return httpx.Response(201, json={**_obs_body(), "id": "server-1"})

    async with _client(handler) as http:
        status, resp = await fhir_conditional_update(
            http, "https://ehr/fhir", "Observation", _obs_body(),
            search_params={"identifier": "urn:healthassistant:observation|abc"},
            access_token="TOK",
        )
    assert status == 201
    assert resp is not None and resp["id"] == "server-1"
    # PUT verb, identifier in query string, bearer + fhir content-type
    assert captured["method"] == "PUT"
    assert "/Observation" in captured["url"]
    assert "identifier=" in captured["url"]
    assert captured["auth"] == "Bearer TOK"
    assert captured["content_type"] == "application/fhir+json"
    assert '"resourceType":"Observation"' in captured["body"].replace(" ", "")


@pytest.mark.asyncio
async def test_conditional_update_update_returns_200():
    async with _client(lambda r: httpx.Response(200, json={**_obs_body(), "id": "x"})) as http:
        status, resp = await fhir_conditional_update(
            http, "https://ehr/fhir", "Observation", _obs_body(),
            search_params={"identifier": "urn:x|abc"},
        )
    assert status == 200
    assert resp is not None


@pytest.mark.asyncio
async def test_conditional_update_412_returns_tuple_not_raise():
    """412 precondition-failed is returned, not raised — caller treats as skip."""
    async with _client(lambda r: httpx.Response(
        412, json={"resourceType": "OperationOutcome", "issue": [{"severity": "warning"}]}
    )) as http:
        status, resp = await fhir_conditional_update(
            http, "https://ehr/fhir", "Observation", _obs_body(),
            search_params={"identifier": "urn:x|abc"},
        )
    assert status == 412
    assert resp is not None and resp["resourceType"] == "OperationOutcome"


@pytest.mark.asyncio
async def test_conditional_update_tokenless_sends_no_bearer():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(201, json=_obs_body())

    async with _client(handler) as http:
        await fhir_conditional_update(
            http, "https://ehr/fhir", "Observation", _obs_body(),
            search_params={"identifier": "urn:x|abc"}, access_token=None,
        )
    assert seen["auth"] is None


@pytest.mark.asyncio
async def test_conditional_update_401_raises_auth_error():
    async with _client(lambda r: httpx.Response(401, text="nope")) as http:
        with pytest.raises(IntegrationAuthError):
            await fhir_conditional_update(
                http, "https://ehr/fhir", "Observation", _obs_body(),
                search_params={"identifier": "urn:x|abc"},
            )


@pytest.mark.asyncio
async def test_conditional_update_400_raises_data_error():
    async with _client(lambda r: httpx.Response(400, text="bad")) as http:
        with pytest.raises(IntegrationDataError):
            await fhir_conditional_update(
                http, "https://ehr/fhir", "Observation", _obs_body(),
                search_params={"identifier": "urn:x|abc"},
            )


@pytest.mark.asyncio
async def test_conditional_update_sends_if_match_header():
    seen = {}

    def handler(request):
        seen["if_match"] = request.headers.get("if-match")
        seen["if_none_match"] = request.headers.get("if-none-match")
        return httpx.Response(200, json=_obs_body())

    async with _client(handler) as http:
        await fhir_conditional_update(
            http, "https://ehr/fhir", "Observation", _obs_body(),
            search_params={"identifier": "urn:x|abc"},
            if_match='W/"3"', if_none_match="*",
        )
    assert seen["if_match"] == 'W/"3"'
    assert seen["if_none_match"] == "*"

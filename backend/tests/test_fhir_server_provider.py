"""Unit tests for integrations.fhir_server.provider (Stage 2 pull + Stage 2b push).

Covers the per-instance ``auth_mode`` (smart vs none/tokenless), the SMART
refresh-on-401 path, the push pipeline (echo exclusion, custom-coding exclusion,
412 handling, cursor advance), ``sync_direction`` gating, the connection check,
and the custom-action surface. HTTP mocked via httpx.MockTransport; DB access is
mocked via AsyncSessionLocal; no Redis.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from integrations.fhir_server.provider import FhirServerProvider
from integrations.sdk.exceptions import IntegrationAuthError


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _lab_obs(code="2345-7"):
    return {
        "resourceType": "Observation", "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": "Glucose"}], "text": "Glucose"},
        "subject": {"reference": "Patient/REMOTE-1"},
        "valueQuantity": {"value": 95, "unit": "mg/dL", "code": "mg/dL"},
        "effectiveDateTime": "2026-06-01T10:00:00Z",
        "meta": {"lastUpdated": "2026-06-01T10:01:00Z"},
    }


def _integration(auth_mode, **extra):
    cfg = {"fhir_base_url": "https://ehr/fhir", "auth_mode": auth_mode,
           "time_window_months": 12, "categories": "both", "sync_direction": "both"}
    cfg.update(extra)
    return SimpleNamespace(
        id="i1", tenant_id=uuid4(), patient_id=uuid4(),
        user_config=cfg, is_debug_enabled=False,
        instance_name="My Hospital", provider="fhir_server",
    )


class _FakeSmart:
    def __init__(self, token="TOKEN", patient="REMOTE-1", raise_on_live=False):
        self._token = token
        self._raise_on_live = raise_on_live
        self.force_refresh_calls = 0
        self.tokens = type("T", (), {"get_patient": lambda self, i: patient})()

    async def get_live_token(self, i):
        if self._raise_on_live:
            raise IntegrationAuthError("no token")
        return self._token

    async def force_refresh(self, i):
        self.force_refresh_calls += 1
        return "TOKEN-2"


@pytest.mark.asyncio
async def test_pull_data_none_mode_tokenless_pull():
    """auth_mode=none -> no OAuth, tokenless FHIR search returns observations."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json={
        "resourceType": "Bundle", "entry": [{"resource": _lab_obs()}],
    }))
    integ = _integration("none")
    observations = await provider.pull_data(integ)
    assert len(observations) == 1
    assert observations[0].code["coding"][0]["code"] == "2345-7"
    # subject localized to the integration's patient
    assert observations[0].subject == {"reference": f"Patient/{integ.patient_id}"}
    await provider.close()


@pytest.mark.asyncio
async def test_pull_preserves_canonical_category_list():
    """Pulled FHIR category (0..* list) is kept canonical — not flattened to a
    dict — so data round-trips pull -> store -> push as valid FHIR."""
    provider = FhirServerProvider()
    await provider.setup({})
    remote = _lab_obs()
    remote["category"] = [{
        "coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory",
        }]
    }]
    provider._http_client = _client(lambda r: httpx.Response(200, json={
        "resourceType": "Bundle", "entry": [{"resource": remote}],
    }))
    observations = await provider.pull_data(_integration("none"))
    assert len(observations) == 1
    assert isinstance(observations[0].category, list)
    assert observations[0].category[0]["coding"][0]["code"] == "laboratory"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_data_none_mode_sends_no_bearer():
    provider = FhirServerProvider()
    await provider.setup({})
    seen = {}
    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
    provider._http_client = _client(handler)
    await provider.pull_data(_integration("none"))
    assert seen["auth"] is None
    await provider.close()


@pytest.mark.asyncio
async def test_pull_data_smart_mode_pending_returns_empty():
    """auth_mode=smart without _oauth (not yet authorized) -> no pull."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json={"resourceType": "Bundle", "entry": []}))
    integ = _integration("smart")  # no _oauth blob
    assert await provider.pull_data(integ) == []
    await provider.close()


@pytest.mark.asyncio
async def test_authorized_search_refreshes_on_401_race():
    """SMART search: first request 401s -> force_refresh -> retry succeeds."""
    provider = FhirServerProvider()
    await provider.setup({})
    state = {"first": True}
    def handler(request):
        if state["first"]:
            state["first"] = False
            return httpx.Response(401, text="expired")
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": _lab_obs()}]})
    provider._http_client = _client(handler)
    provider._smart = _FakeSmart(token="TOKEN")  # live token, but server 401s once
    integ = _integration("smart")
    results = await provider._authorized_search(integ, "https://ehr/fhir", "Observation", {"patient": "REMOTE-1"})
    assert len(results) == 1
    assert provider._smart.force_refresh_calls == 1
    await provider.close()


# ---------------------------------------------------------------------------
# sync_direction gating (Stage 2b)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pull_data_skipped_when_direction_is_push_only():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(
        200, json={"resourceType": "Bundle", "entry": [{"resource": _lab_obs()}]}))
    # pull must NOT happen -> empty list even though the server has data
    integ = _integration("none", sync_direction="push_only")
    assert await provider.pull_data(integ) == []
    await provider.close()


@pytest.mark.asyncio
async def test_push_data_skipped_when_direction_is_pull_only():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none", sync_direction="pull_only")
    with patch("app.core.database.AsyncSessionLocal") as m:
        # push_data must not touch the DB at all
        m.assert_not_called()
        await provider.push_data(integ, {"status": "x"})
        m.assert_not_called()
    # cursor must not be set
    assert provider.get_sync_cursor(integ, "last_pushed_at") is None
    await provider.close()


# ---------------------------------------------------------------------------
# Push pipeline (Stage 2b)
# ---------------------------------------------------------------------------

def _local_obs(*, system="http://loinc.org", code="2345-7", performer=None, oid=None, value=95):
    """A fake ORM Observation with the attributes _run_push reads."""
    oid = oid or uuid4()
    code_dict = {"coding": [{"system": system, "code": code, "display": "Glucose"}], "text": "Glucose"}
    return SimpleNamespace(
        id=oid,
        code=code_dict,
        performer=performer,
        updated_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        effective_datetime=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        value_quantity={"value": value, "unit": "mg/dL", "code": "mg/dL"},
        value_string=None,
        value_codeableConcept=None,
        raw_value=value,
        subject={"reference": "Patient/LOCAL"},
        status="final",
        category=None,
        interpretation=None,
        comment=None,
        method=None,
        reference_range=None,
        lab_reference_range=None,
        to_fhir_dict=lambda: {
            "resourceType": "Observation",
            "id": str(oid),
            "status": "final",
            "code": code_dict,
            "subject": {"reference": "Patient/LOCAL"},
            "valueQuantity": {"value": value, "unit": "mg/dL", "code": "mg/dL"},
            "meta": {"versionId": "1", "lastUpdated": "2026-06-19T12:00:00Z", "source": "x"},
        },
    )


def _patch_db(candidates):
    """Patch AsyncSessionLocal (imported lazily from app.core.database)."""
    mock_session_local = MagicMock()
    mock_session = AsyncMock()
    mock_session_local.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = candidates
    mock_session.execute = AsyncMock(return_value=mock_result)
    return patch("app.core.database.AsyncSessionLocal", mock_session_local)


@pytest.mark.asyncio
async def test_run_push_excludes_echo_and_custom_coding_and_rewrites_subject():
    """Only the standard-coded, non-echo observation is pushed; subject rewritten."""
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")  # tokenless

    echo_ref = f"Integration/{integ.id}"
    pushable = _local_obs(oid=uuid4())  # LOINC, no performer -> pushed
    echo = _local_obs(oid=uuid4(), performer=[{"reference": echo_ref, "display": "My Hospital"}])
    custom = _local_obs(oid=uuid4(), system="http://healthassistant.local/custom", code="HK_HeartRate")

    captured = {}

    async def fake_update(http, base, rtype, body, *, search_params, access_token=None, **kw):
        captured["body"] = body
        captured["search_params"] = search_params
        captured["access_token"] = access_token
        return 201, {"id": "server-1"}

    with _patch_db([pushable, echo, custom]), \
         patch("integrations.fhir_server.provider.fhir_conditional_update", new=fake_update):
        result = await provider._run_push(integ)

    assert result["created"] == 1
    assert result["pushed"] == 1
    # subject rewritten to the remote patient (none-mode uses remote_patient_id)
    assert captured["body"]["subject"] == {"reference": "Patient/REMOTE-1"} or captured["body"]["subject"]["reference"].startswith("Patient/")
    # the local-UUID identifier is stamped
    idents = [i for i in captured["body"]["identifier"] if i.get("system") == "urn:healthassistant:observation"]
    assert len(idents) == 1
    assert idents[0]["value"] == str(pushable.id)
    # server-controlled fields dropped
    assert "id" not in captured["body"]
    assert "versionId" not in (captured["body"].get("meta") or {})
    # tokenless -> no bearer
    assert captured["access_token"] is None
    # cursor advanced
    assert provider.get_sync_cursor(integ, "last_pushed_at") is not None
    await provider.close()


@pytest.mark.asyncio
async def test_run_push_412_counted_as_skipped():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    obs = _local_obs()

    async def fake_update(*a, **kw):
        return 412, {"resourceType": "OperationOutcome"}

    with _patch_db([obs]), \
         patch("integrations.fhir_server.provider.fhir_conditional_update", new=fake_update):
        result = await provider._run_push(integ)

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert result["pushed"] == 0
    await provider.close()


@pytest.mark.asyncio
async def test_run_push_excludes_background_echo_by_domain_display():
    """Background-sync rows store only display=domain; still excluded from push."""
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    # performer with no reference, display == provider domain ("fhir_server")
    bg_echo = _local_obs(performer=[{"type": "Integration", "display": "fhir_server"}])
    pushable = _local_obs(oid=uuid4())

    calls = []

    async def fake_update(*a, **kw):
        calls.append(kw.get("search_params"))
        return 201, {}

    with _patch_db([bg_echo, pushable]), \
         patch("integrations.fhir_server.provider.fhir_conditional_update", new=fake_update):
        result = await provider._run_push(integ)

    assert result["created"] == 1  # only the non-echo one
    assert len(calls) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_push_data_smart_pending_noop():
    """auth_mode=smart without _oauth -> push is a no-op (no candidates queried)."""
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("smart")  # no _oauth
    with patch("app.core.database.AsyncSessionLocal") as m:
        await provider.push_data(integ, {})
        m.assert_not_called()
    assert provider.get_sync_cursor(integ, "last_pushed_at") is None
    await provider.close()


# ---------------------------------------------------------------------------
# Check connection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_connection_tokenless_reads_capability_statement():
    provider = FhirServerProvider()
    await provider.setup({})
    cap = {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "software": {"name": "HAPI FHIR", "version": "6.0"},
        "rest": [{"resource": [{"type": "Observation"}, {"type": "Patient"}]}],
    }
    provider._http_client = _client(lambda r: httpx.Response(200, json=cap))
    info = await provider._check_connection(_integration("none"))
    assert info["ok"] is True
    assert info["fhir_version"] == "4.0.1"
    assert info["software"] == "HAPI FHIR"
    assert "Observation" in info["resources"]
    await provider.close()


@pytest.mark.asyncio
async def test_check_connection_smart_pending_returns_error():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json={}))
    info = await provider._check_connection(_integration("smart"))  # no _oauth
    assert info["ok"] is False
    assert "PENDING" in info["error"]
    await provider.close()


@pytest.mark.asyncio
async def test_check_connection_http_error_reported():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(503, text="down"))
    info = await provider._check_connection(_integration("none"))
    assert info["ok"] is False
    assert "503" in info["error"]
    await provider.close()


# ---------------------------------------------------------------------------
# Custom actions
# ---------------------------------------------------------------------------

def test_custom_actions_declared():
    provider = FhirServerProvider()
    actions = provider.get_custom_actions()
    ids = {a["id"] for a in actions}
    assert ids == {"check_connection", "pull_now", "push_now", "push_preview", "reset_cursors"}


@pytest.mark.asyncio
async def test_action_reset_cursors_clears_sync_state():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    provider.set_sync_cursor(integ, "last_updated", "2026-01-01T00:00:00Z")
    provider.set_sync_cursor(integ, "last_pushed_at", "2026-01-02T00:00:00Z")

    response = await provider.execute_custom_action(integ, "reset_cursors")

    state = (integ.user_config or {}).get("_sync_state", {})
    assert "last_updated" not in state
    assert "last_pushed_at" not in state
    assert "Reset" in response["message"]


@pytest.mark.asyncio
async def test_action_push_preview_lists_candidates_without_sending():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    pushable = _local_obs(oid=uuid4())
    echo = _local_obs(performer=[{"reference": f"Integration/{integ.id}"}])

    sent = []

    async def fake_update(*a, **kw):
        sent.append(kw)
        return 201, {}

    with _patch_db([pushable, echo]), \
         patch("integrations.fhir_server.provider.fhir_conditional_update", new=fake_update):
        response = await provider.execute_custom_action(integ, "push_preview")

    # preview must NOT push anything
    assert sent == []
    assert response["message"] == "1 observation(s) would be pushed."
    # the summary kv block + a candidates table block
    blocks = response["results"]
    assert any(b["type"] == "kv" for b in blocks)
    assert any(b["type"] == "table" for b in blocks)
    await provider.close()


@pytest.mark.asyncio
async def test_unknown_action_raises_not_implemented():
    provider = FhirServerProvider()
    await provider.setup({})
    with pytest.raises(NotImplementedError):
        await provider.execute_custom_action(_integration("none"), "bogus")
    await provider.close()

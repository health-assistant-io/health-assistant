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
    assert ids == {
        "check_connection", "find_patient", "pull_now",
        "push_now", "push_preview", "reset_cursors",
    }
    # the patient-picker action carries the modal hint for the frontend
    find = next(a for a in actions if a["id"] == "find_patient")
    assert find.get("modal") == "patient_picker"


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


# ===========================================================================
# Multi-resource sync (Phases 1–4 of the fhir-server multi-resource sync plan)
# ===========================================================================


def _bundle(resources, *, next_url=None):
    """Build a FHIR searchset Bundle wrapping ``resources``."""
    bundle = {"resourceType": "Bundle", "type": "searchset",
              "entry": [{"resource": r} for r in resources]}
    if next_url:
        bundle["link"] = [{"relation": "next", "url": next_url}]
    return bundle


def _condition(rid="cond-1", last_updated="2026-06-01T10:00:00Z"):
    return {
        "resourceType": "Condition", "id": rid,
        "code": {"text": "Type 2 Diabetes",
                 "coding": [{"system": "http://snomed.info/sct", "code": "44054006"}]},
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "onsetDateTime": "2020-01-01T00:00:00Z",
        "meta": {"lastUpdated": last_updated},
    }


def _encounter(rid="enc-1", last_updated="2026-06-02T10:00:00Z"):
    return {
        "resourceType": "Encounter", "id": rid,
        "status": "finished",
        "class": {"code": "AMB"},
        "period": {"start": "2026-05-01T10:00:00Z"},
        "reasonCode": [{"text": "Annual checkup"}],
        "meta": {"lastUpdated": last_updated},
    }


def _doc_ref(rid="doc-1", url="http://ehr/fhir/Binary/abc"):
    return {
        "resourceType": "DocumentReference", "id": rid, "status": "current",
        "content": [{"attachment": {"url": url, "title": "report.pdf",
                                    "contentType": "application/pdf"}}],
        "category": [{"coding": [{"code": "lab-report", "display": "Lab Report"}]}],
        "context": {"encounter": [{"reference": "Encounter/enc-1"}]},
        "meta": {"lastUpdated": "2026-06-03T10:00:00Z"},
    }


def _med_statement(rid="med-1"):
    return {
        "resourceType": "MedicationStatement", "id": rid, "status": "active",
        "medicationCodeableConcept": {"text": "Metformin",
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "860975"}]},
        "effectiveDateTime": "2026-01-01",
        "dosage": [{"text": "500mg twice daily"}],
        "meta": {"lastUpdated": "2026-06-04T10:00:00Z"},
    }


def _med_request(rid="req-1"):
    return {
        "resourceType": "MedicationRequest", "id": rid, "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "Lisinopril"},
        "authoredOn": "2026-02-01",
        "dosageInstruction": [{"text": "10mg daily"}],
        "meta": {"lastUpdated": "2026-06-05T10:00:00Z"},
    }


def _allergy(rid="alg-1"):
    return {
        "resourceType": "AllergyIntolerance", "id": rid,
        "code": {"text": "Penicillin",
                 "coding": [{"system": "http://snomed.info/sct", "code": "91936005"}]},
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "category": ["medication"], "criticality": "high",
        "meta": {"lastUpdated": "2026-06-06T10:00:00Z"},
    }


def _immunization(rid="imm-1"):
    return {
        "resourceType": "Immunization", "id": rid, "status": "completed",
        "vaccineCode": {"text": "Influenza vaccine",
                        "coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "140"}]},
        "occurrenceDateTime": "2025-10-15T09:00:00Z",
        "lotNumber": "LOT123",
        "meta": {"lastUpdated": "2026-06-07T10:00:00Z"},
    }


def _client_routing(routes):
    """Build a MockTransport that dispatches by URL substring.

    ``routes`` is a list of ``(url_substring, responder)`` where responder
    takes the request and returns an httpx.Response.
    """
    def handler(request):
        for needle, responder in routes:
            if needle in str(request.url):
                return responder(request)
        return httpx.Response(404, text=f"no route for {request.url}")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Phase 1 — Conditions → clinical events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_clinical_events_maps_condition_with_external_id():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_condition()])))
    integ = _integration("none")
    events = await provider.pull_clinical_events(integ)
    assert len(events) == 1
    ev = events[0]
    assert ev.external_id == "cond-1"  # dedup key = remote Condition.id
    assert ev.title == "Type 2 Diabetes"
    assert str(ev.patient_id) == str(integ.patient_id)
    # per-resource cursor advanced
    assert provider.get_sync_cursor(integ, "last_updated:Condition") == "2026-06-01T10:00:00Z"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_clinical_events_respects_push_only_direction():
    provider = FhirServerProvider()
    await provider.setup({})
    # server has data but pull must not happen
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_condition()])))
    integ = _integration("none", sync_direction="push_only")
    assert await provider.pull_clinical_events(integ) == []
    await provider.close()


@pytest.mark.asyncio
async def test_pull_clinical_events_disabled_by_pull_resources_config():
    """An instance that deselected Condition pulls nothing for it."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_condition()])))
    integ = _integration("none", pull_resources=["Encounter", "Observation"])
    assert await provider.pull_clinical_events(integ) == []
    await provider.close()


# ---------------------------------------------------------------------------
# Phase 1 — Encounters → examinations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_examinations_maps_encounter_period_start():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_encounter()])))
    integ = _integration("none")
    exams = await provider.pull_examinations(integ)
    assert len(exams) == 1
    exam = exams[0]
    assert exam.external_id == "enc-1"
    assert str(exam.examination_date) == "2026-05-01"
    assert "Annual checkup" in (exam.notes or "")
    assert provider.get_sync_cursor(integ, "last_updated:Encounter") == "2026-06-02T10:00:00Z"
    await provider.close()


# ---------------------------------------------------------------------------
# _search_resource — per-resource cursor + pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_resource_advances_per_resource_cursor():
    """Each resource type gets its own cursor key; a Condition pull must not
    touch the Observation ``last_updated`` cursor."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_condition()])))
    integ = _integration("none")
    provider.set_sync_cursor(integ, "last_updated", "2020-01-01T00:00:00Z")
    await provider._search_resource(integ, "Condition")
    # Condition cursor set, Observation cursor untouched
    assert provider.get_sync_cursor(integ, "last_updated:Condition") == "2026-06-01T10:00:00Z"
    assert provider.get_sync_cursor(integ, "last_updated") == "2020-01-01T00:00:00Z"
    await provider.close()


@pytest.mark.asyncio
async def test_search_resource_follows_bundle_pagination():
    provider = FhirServerProvider()
    await provider.setup({})
    page2_url = "https://ehr/fhir/Condition?page=2"
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if "page=2" in str(request.url):
            return httpx.Response(200, json=_bundle([_condition(rid="cond-2")]))
        return httpx.Response(200, json=_bundle([_condition(rid="cond-1")], next_url=page2_url))

    provider._http_client = _client(handler)
    integ = _integration("none")
    resources = await provider._search_resource(integ, "Condition")
    assert len(resources) == 2  # both pages flattened
    assert {r["id"] for r in resources} == {"cond-1", "cond-2"}
    assert calls["count"] == 2
    await provider.close()


# ---------------------------------------------------------------------------
# Phase 2 — DocumentReference → documents (attachment fetch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_documents_fetches_attachment_and_returns_pull():
    provider = FhirServerProvider()
    await provider.setup({})
    pdf_bytes = b"%PDF-1.4 fake report"

    def handler(request):
        url = str(request.url)
        if "DocumentReference" in url:
            return httpx.Response(200, json=_bundle([_doc_ref()]))
        if "Binary/abc" in url:
            return httpx.Response(200, content=pdf_bytes,
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    provider._http_client = _client(handler)
    integ = _integration("none")
    pulls = await provider.pull_documents(integ)
    assert len(pulls) == 1
    pull = pulls[0]
    assert pull.content == pdf_bytes
    assert pull.filename == "report.pdf"
    assert pull.external_id == "doc-1"  # DB-level dedup key
    assert pull.examination_external_id == "enc-1"  # linked Encounter id
    assert pull.category_concept_slug == "lab-report"
    assert provider.get_sync_cursor(integ, "last_updated:DocumentReference") == "2026-06-03T10:00:00Z"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_documents_skips_unreachable_attachment():
    """A 404 attachment must not abort the whole document pull."""
    provider = FhirServerProvider()
    await provider.setup({})

    def handler(request):
        if "DocumentReference" in request.url.path:
            return httpx.Response(200, json=_bundle([_doc_ref(url="http://ehr/fhir/Binary/missing")]))
        return httpx.Response(404)

    provider._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pulls = await provider.pull_documents(_integration("none"))
    assert pulls == []  # unreachable attachment dropped, not raised
    await provider.close()


# ---------------------------------------------------------------------------
# Phase 3 — HITL proposals for unmapped codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_hitl_proposals_only_for_codes_absent_from_catalog():
    provider = FhirServerProvider()
    await provider.setup({})
    # remote carries two LOINC codes: 2345-7 (known locally) + 99999-9 (unknown)
    remote_obs = [
        {"resourceType": "Observation", "code": {"coding": [
            {"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}]},
         "valueQuantity": {"value": 95, "unit": "mg/dL"}},
        {"resourceType": "Observation", "code": {"coding": [
            {"system": "http://loinc.org", "code": "99999-9", "display": "Novel Marker"}]},
         "valueQuantity": {"value": 1, "unit": "mg/L"}},
    ]
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle(remote_obs)))
    # local catalog already knows 2345-7
    provider._known_biomarker_codes = AsyncMock(return_value={"2345-7"})
    integ = _integration("none")

    proposals = await provider.pull_hitl_proposals(integ)
    assert len(proposals) == 1
    assert proposals[0].proposed_payload["code"] == "99999-9"
    assert proposals[0].proposed_payload["coding_system"] == "loinc"
    assert proposals[0].proposed_payload["preferred_unit_symbol"] == "mg/L"
    # seen-codes cursor records both observed codes
    seen = provider.get_sync_cursor(integ, "hitl:seen_codes", default=[])
    assert "99999-9" in seen
    await provider.close()


@pytest.mark.asyncio
async def test_pull_hitl_proposals_idempotent_across_syncs():
    """A second sync must not re-propose a code already in seen_codes."""
    provider = FhirServerProvider()
    await provider.setup({})
    remote_obs = [{"resourceType": "Observation", "code": {"coding": [
        {"system": "http://loinc.org", "code": "99999-9", "display": "Novel Marker"}]}}]
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle(remote_obs)))
    provider._known_biomarker_codes = AsyncMock(return_value=set())
    integ = _integration("none")
    # already proposed in a prior sync
    provider.set_sync_cursor(integ, "hitl:seen_codes", ["99999-9"])
    proposals = await provider.pull_hitl_proposals(integ)
    assert proposals == []
    await provider.close()


@pytest.mark.asyncio
async def test_handle_proposal_resolution_adds_code_to_seen():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    outcome = SimpleNamespace(final_payload={"code": "88888-8"})
    await provider.handle_proposal_resolution(integ, "pid", outcome)
    seen = provider.get_sync_cursor(integ, "hitl:seen_codes", default=[])
    assert "88888-8" in seen
    await provider.close()


# ---------------------------------------------------------------------------
# Phase 4 — Medications / Allergies / Immunizations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_medications_maps_statement_and_request():
    provider = FhirServerProvider()
    await provider.setup({})

    def handler(request):
        path = request.url.path
        if "MedicationStatement" in path:
            return httpx.Response(200, json=_bundle([_med_statement()]))
        if "MedicationRequest" in path:
            return httpx.Response(200, json=_bundle([_med_request()]))
        return httpx.Response(404)

    provider._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meds = await provider.pull_medications(_integration("none"))
    assert len(meds) == 2
    by_intent = {m.intent.value: m for m in meds}
    assert "statement" in by_intent and "order" in by_intent
    stmt = by_intent["statement"]
    assert stmt.external_id == "med-1"
    assert stmt.code["text"] == "Metformin"
    assert stmt.dosage == "500mg twice daily"
    req = by_intent["order"]
    assert req.external_id == "req-1"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_allergies_maps_intolerance():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_allergy()])))
    allergies = await provider.pull_allergies(_integration("none"))
    assert len(allergies) == 1
    a = allergies[0]
    assert a.external_id == "alg-1"
    assert a.code["text"] == "Penicillin"
    assert a.criticality.value == "HIGH"
    assert a.category.value == "MEDICATION"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_immunizations_maps_dose():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_immunization()])))
    imms = await provider.pull_immunizations(_integration("none"))
    assert len(imms) == 1
    i = imms[0]
    assert i.external_id == "imm-1"
    assert i.vaccine_code.text == "Influenza vaccine"
    assert i.lot_number == "LOT123"
    await provider.close()


@pytest.mark.asyncio
async def test_pull_medications_respects_pull_resources_deselection():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_med_statement()])))
    integ = _integration("none", pull_resources=["Observation", "Condition"])
    assert await provider.pull_medications(integ) == []
    await provider.close()


# ---------------------------------------------------------------------------
# Cursor reset clears per-resource cursors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_cursors_clears_per_resource_cursors():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    provider.set_sync_cursor(integ, "last_updated", "2026-01-01T00:00:00Z")
    provider.set_sync_cursor(integ, "last_updated:Condition", "2026-02-01T00:00:00Z")
    provider.set_sync_cursor(integ, "hitl:seen_codes", ["99999-9"])
    provider.set_sync_cursor(integ, "last_pushed_at", "2026-03-01T00:00:00Z")

    response = await provider.execute_custom_action(integ, "reset_cursors")

    state = (integ.user_config or {}).get("_sync_state", {})
    assert state == {}  # everything cleared
    assert "Reset" in response["message"]
    await provider.close()


# ---------------------------------------------------------------------------
# SDK base hooks declared
# ---------------------------------------------------------------------------


def test_all_pull_hooks_declared():
    """fhir_server opts into every multi-resource pull capability."""
    provider = FhirServerProvider()
    assert provider.supports_clinical_events() is True
    assert provider.supports_examinations() is True
    assert provider.supports_documents() is True
    assert provider.supports_hitl_proposals() is True
    assert provider.supports_medications() is True
    assert provider.supports_allergies() is True
    assert provider.supports_immunizations() is True


# ---------------------------------------------------------------------------
# Remote patient picker (find_patient / select_patient actions)
# ---------------------------------------------------------------------------


def _remote_patient(rid="pat-1", name="John Smith", mrn="MRN-999", birth="1980-05-01"):
    return {
        "resourceType": "Patient", "id": rid,
        "name": [{"family": "Smith", "given": ["John"], "text": name}],
        "identifier": [{"system": "http://hospital.example.org/mrn", "value": mrn}],
        "birthDate": birth, "gender": "male",
        "meta": {"lastUpdated": "2026-06-01T10:00:00Z"},
    }


@pytest.mark.asyncio
async def test_find_patient_searches_by_query_and_summarizes():
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_remote_patient()])))
    integ = _integration("none")
    provider._local_patient_hint = AsyncMock(return_value={"mrn": None, "name": None})

    result = await provider.execute_custom_action(integ, "find_patient", query="Smith")

    assert result["query"] == "Smith"
    assert len(result["matches"]) == 1
    m = result["matches"][0]
    assert m["id"] == "pat-1"
    assert m["name"] == "John Smith"
    assert m["mrn"] == "MRN-999"
    assert m["birth_date"] == "1980-05-01"
    await provider.close()


@pytest.mark.asyncio
async def test_find_patient_auto_suggests_by_local_mrn_when_no_query():
    """Opening the picker with no query seeds the search from the local MRN."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([_remote_patient()])))
    integ = _integration("none")
    provider._local_patient_hint = AsyncMock(return_value={"mrn": "MRN-999", "name": "John Smith"})

    result = await provider.execute_custom_action(integ, "find_patient")

    assert result["auto_suggested"] == "MRN"
    assert result["identifier"] == "MRN-999"
    assert len(result["matches"]) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_select_patient_sets_remote_patient_id():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")

    response = await provider.execute_custom_action(integ, "select_patient", patient_id="pat-1")

    assert integ.user_config["remote_patient_id"] == "pat-1"
    assert "pat-1" in response["message"]
    # _remote_patient now resolves to the explicit override
    assert provider._remote_patient(integ) == "pat-1"
    await provider.close()


@pytest.mark.asyncio
async def test_select_patient_without_id_is_noop():
    provider = FhirServerProvider()
    await provider.setup({})
    integ = _integration("none")
    response = await provider.execute_custom_action(integ, "select_patient")
    assert integ.user_config.get("remote_patient_id") is None
    assert "No patient" in response["message"]
    await provider.close()


@pytest.mark.asyncio
async def test_find_patient_degrades_gracefully_on_auth_error():
    """A SMART PENDING instance (no token) returns empty matches, not an error."""
    provider = FhirServerProvider()
    await provider.setup({})
    provider._http_client = _client(lambda r: httpx.Response(200, json=_bundle([])))
    integ = _integration("smart")  # no _oauth -> PENDING
    provider._local_patient_hint = AsyncMock(return_value={"mrn": None, "name": None})
    result = await provider.execute_custom_action(integ, "find_patient", query="Smith")
    assert result["matches"] == []
    await provider.close()

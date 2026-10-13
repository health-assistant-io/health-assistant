"""Uniform BYOK contract-test matrix (family plan 17 Layer 4, §15).

Ported from the study port (test_ai_provider_setup_contract.py — the Python
reference) onto health's async stack. Health deltas recorded per plan 17
Phase 3: USER-scope slot mapping (chat→default, vision→ocr, stt→
transcription), gemini/anthropic presets overlay-disabled (no wired
builders), USER-scope isolation cases added on top.

Case manifest (normative semantics, names indicative):

1.  happy path per preset — curated persisted, caps inferred, defaults bound;
2.  preset metadata uniformity (health overlay: gemini/anthropic disabled
    with recorded reasons; openai_compatible → ProviderType.OPENAI);
3.  idempotent re-run appends the fetched catalog, never replaces, dedupes;
4.  manual-row adoption (earliest first) + preset_key stamp;
5.  never clobbers live assignments (default/vision/stt USER slots);
6.  empty slots gap-fill — dead/missing model ids rebind;
7.  invalid key → classified error, persists nothing;
8.  unknown preset → typed 404, no fetch;
9.  hanging fetch → timeout; local refused → local_not_running;
10. classifier table incl. suspectedVendor prefix hints;
11. snapshot-suffix curation match + zero-match fallback (curated_missed);
12. capability inference split (whisper-1 → stt; tts-1 → tts; embeddings);
13. set-default: task-scoped binding with capability guards + cross-provider
    rejection (409);
14. USER-scope isolation — SYSTEM/TENANT assignments and other users' rows
    are never clobbered by a setup.

Plan 26 / ADR-0032 additions (decision capability + slots, scope-aware D17):

15. bind_decision capability-aware gap-fill into the four ``decision_*``
    task slots — at the SAME scope, never ``open-args``, rows stay
    PHI-excluded;
16. bind_decision=false persists the decision models, binds no slots;
17. live decision slots never clobbered (fill empty only);
18. decision-only preset (wire type ``decision``) validates via the decision
    probe — ok path persists + binds; failure persists nothing;
19. probe timeout classifies ``timeout``;
20. probe is no oracle without a valid key (403 → unknown; 402 →
    insufficient_credit);
21. slot separation enforced both directions + ``open-args`` never
    assignable + the summary never inherits the ``default`` chat fallback;
22. preset catalog decision data shape (provisional rides ``presets``, not
    ``presetOrder``);
23. PHI scope guard (D17): assignments are PHI-excluded by default; the
    explicit ``phi_allowed`` opt-in exists on decision_* task types only.
"""

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.ai.providers import setup as byok_setup
from app.ai.providers.errors import classify_provider_error, extract_error_status
from app.ai.providers.presets import (
    DISABLED_PRESET_REASONS,
    KEY_PREFIX_HINTS,
    PRESET_ORDER,
    SETUP_PRESETS,
    guess_preset_for_key,
)
from app.ai.providers.setup import infer_caps
from app.core.database import AsyncSessionLocal
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AIScope,
    AITaskAssignment,
)
from app.models.tenant_model import TenantModel
from tests._auth_helpers import headers_for_claims

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def allow_local_preset_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt the SSRF guard's local hosts in for this module.

    The §15 matrix includes ``local`` presets (Ollama on ``localhost``), whose
    loopback target is allowed in real deployments via
    ``INTEGRATION_ALLOWED_HOSTS`` / ``DEBUG``. ``net_guard`` reads the env var
    per call, so declaring it here keeps the contract tests independent of the
    developer ``.env`` (absent on CI, where ``DEBUG=false`` and the guard
    would otherwise block the preset before the mocked fetch runs).
    """
    monkeypatch.setenv("INTEGRATION_ALLOWED_HOSTS", "localhost")


@pytest_asyncio.fixture
async def user_ctx():
    """A real tenant + USER-role JWT + a raw id pair for direct assertions."""
    from tests._auth_helpers import auth_headers, create_user

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tenant_id, name="BYOK Tenant", slug=f"byok-{tenant_id}"))
        await session.commit()

    user = await create_user(
        role="USER", tenant_id=tenant_id, user_id=user_id, email="byok@test.local"
    )
    ctx = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "headers": await auth_headers(user),
    }
    yield ctx

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AITaskAssignment).where(AITaskAssignment.tenant_id == tenant_id)
        )
        await session.execute(delete(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id))
        await session.commit()


async def _seed_provider(
    tenant_id,
    user_id,
    *,
    name="Manual row",
    provider_type="openai",
    api_base="https://api.openai.com/v1",
    preset_key=None,
) -> AIProviderModel:
    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name=name,
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_type=provider_type,
            api_base=api_base,
            api_key=None,
            preset_key=preset_key,
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        return provider


async def _seed_model(provider_id, model_name, caps) -> AIModel:
    async with AsyncSessionLocal() as session:
        model = AIModel(
            provider_id=provider_id,
            name=model_name,
            model_name=model_name,
            capabilities=caps,
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
        return model


async def _seed_slot(tenant_id, user_id, provider_id, model_id, task_type) -> AITaskAssignment:
    async with AsyncSessionLocal() as session:
        row = AITaskAssignment(
            task_type=task_type,
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_id=provider_id,
            model_id=model_id,
            is_active=True,
            priority=0,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


def wire_catalog(model_ids: list[str]) -> list[byok_setup.RemoteModel]:
    return [byok_setup.RemoteModel(external_id=m, caps=tuple(infer_caps(m))) for m in model_ids]


def install_catalog(
    monkeypatch: pytest.MonkeyPatch,
    result: list[byok_setup.RemoteModel] | Exception,
) -> list[tuple[str, str, str | None]]:
    """Replace the catalog fetch with a canned result; record the calls."""
    calls: list[tuple[str, str, str | None]] = []

    async def fake_fetch(
        wire_type: str, base_url: str, api_key: str | None, transport: Any = None
    ) -> list[byok_setup.RemoteModel]:
        calls.append((wire_type, base_url, api_key))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(byok_setup, "fetch_remote_models", fake_fetch)
    return calls


async def user_rows(tenant_id) -> tuple[list[AIProviderModel], list[AIModel]]:
    async with AsyncSessionLocal() as session:
        providers = (
            (
                await session.execute(
                    select(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        models = (await session.execute(select(AIModel))).scalars().all()
    return list(providers), list(models)


async def slot_of(tenant_id, user_id, task_type) -> AITaskAssignment | None:
    async with AsyncSessionLocal() as session:
        return (
            (
                await session.execute(
                    select(AITaskAssignment)
                    .where(
                        AITaskAssignment.tenant_id == tenant_id,
                        AITaskAssignment.user_id == user_id,
                        AITaskAssignment.task_type == task_type,
                        AITaskAssignment.is_active.is_(True),
                    )
                    .order_by(AITaskAssignment.priority.desc())
                )
            )
            .scalars()
            .first()
        )


# ---------------------------------------------------------------------------
# case 2 — preset metadata uniformity (health overlay)
# ---------------------------------------------------------------------------


def test_preset_metadata_uniformity() -> None:
    assert list(SETUP_PRESETS) == [k for k in PRESET_ORDER if k not in DISABLED_PRESET_REASONS]
    assert sorted(SETUP_PRESETS) == [
        "deepseek",
        "groq",
        "mistral",
        "ollama",
        "openai",
        "openrouter",
    ]
    # health delta: gemini/anthropic overlay-disabled with recorded reasons
    assert set(DISABLED_PRESET_REASONS) == {"gemini", "anthropic"}
    assert DISABLED_PRESET_REASONS["gemini"]
    assert DISABLED_PRESET_REASONS["anthropic"]
    assert SETUP_PRESETS["ollama"]["local"] is True
    # wire type → health's provider-type enum
    assert SETUP_PRESETS["openai"]["type"] == "openai"
    assert SETUP_PRESETS["openai"]["wire_type"] == "openai_compatible"
    assert SETUP_PRESETS["openai"]["stt_model"] == "whisper-1"
    assert [hint["prefix"] for hint in KEY_PREFIX_HINTS] == [
        "sk-ant-",
        "sk-or-v1-",
        "gsk_",
        "AIza",
        "sk-",
    ]
    assert guess_preset_for_key("sk-ant-api03-xyz") == "anthropic"
    assert guess_preset_for_key("gsk_abc") == "groq"
    assert guess_preset_for_key("totally-random") is None
    assert guess_preset_for_key("") is None


# ---------------------------------------------------------------------------
# case 1 — happy path
# ---------------------------------------------------------------------------


async def test_case_1_happy_path_bindings(user_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    calls = install_catalog(
        monkeypatch,
        wire_catalog(["gpt-5.6-terra", "gpt-5.6-luna", "whisper-1"]),
    )
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openai",
            "  sk-live-key  ",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert outcome.catalog_count == 3
    assert outcome.assigned_chat_model == "gpt-5.6-terra"
    assert outcome.assigned_vision_model == "gpt-5.6-terra"
    assert outcome.assigned_stt_model == "whisper-1"
    assert calls == [("openai_compatible", "https://api.openai.com/v1", "sk-live-key")]

    providers, models = await user_rows(tenant_id)
    assert len(providers) == 1
    provider = providers[0]
    assert provider.preset_key == "openai"
    assert provider.scope == AIScope.USER
    assert provider.user_id == user_id
    # key stored encrypted at rest — never plaintext
    assert provider.api_key != "sk-live-key"
    assert provider.get_api_key_plaintext() == "sk-live-key"

    by_wire = {m.model_name: m for m in models}
    assert by_wire["whisper-1"].get_capabilities() == ["stt"]
    assert by_wire["gpt-5.6-terra"].get_capabilities() == ["text", "vision", "tools"]

    default_slot = await slot_of(tenant_id, user_id, "default")
    ocr_slot = await slot_of(tenant_id, user_id, "ocr")
    stt_slot = await slot_of(tenant_id, user_id, "transcription")
    assert default_slot.model_id == by_wire["gpt-5.6-terra"].id
    assert ocr_slot.model_id == by_wire["gpt-5.6-terra"].id
    assert stt_slot.model_id == by_wire["whisper-1"].id


async def test_case_1_happy_path_ollama_keyless(user_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    calls = install_catalog(monkeypatch, wire_catalog(["llama3.3"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "ollama", "", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    providers, _ = await user_rows(tenant_id)
    provider = providers[0]
    assert provider.is_local is True
    assert provider.get_api_key_plaintext() is None
    # no preferred / curated on ollama → nothing binds
    assert outcome.assigned_chat_model is None
    assert calls[0][2] is None


# ---------------------------------------------------------------------------
# case 3 — append-union on re-run
# ---------------------------------------------------------------------------


async def test_case_3_re_setup_appends_the_fetched_catalog_and_dedupes(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "gpt-5.6-luna"]))
    async with AsyncSessionLocal() as db:
        first = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-first", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )
    manual_model = await _seed_model(first.provider_id, "old-favorite", ["text"])

    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "gpt-5.6-sol"]))
    async with AsyncSessionLocal() as db:
        await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-second", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    providers, models = await user_rows(tenant_id)
    assert len(providers) == 1
    provider = providers[0]
    assert provider.get_api_key_plaintext() == "sk-second"
    wire_ids = sorted(m.model_name for m in models if m.provider_id == provider.id)
    assert wire_ids == ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "old-favorite"]
    # user-added models are never deleted by setup
    async with AsyncSessionLocal() as session:
        assert await session.get(AIModel, manual_model.id) is not None
    terra_rows = [m for m in models if m.model_name == "gpt-5.6-terra"]
    assert len(terra_rows) == 1


# ---------------------------------------------------------------------------
# case 4 — manual-row adoption, earliest first
# ---------------------------------------------------------------------------


async def test_case_4_adopts_manual_row_earliest_first_and_stamps(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra"]))
    earliest = await _seed_provider(tenant_id, user_id, name="My OpenAI")
    later = await _seed_provider(tenant_id, user_id, name="Other OpenAI")

    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-new", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    assert outcome.provider_id == earliest.id
    providers, _ = await user_rows(tenant_id)
    by_id = {p.id: p for p in providers}
    assert by_id[earliest.id].preset_key == "openai"
    assert by_id[earliest.id].name == "My OpenAI"
    assert by_id[later.id].preset_key is None


# ---------------------------------------------------------------------------
# case 5 — never clobbers live assignments
# ---------------------------------------------------------------------------


async def test_case_5_never_clobbers_live_assignments(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"]))
    provider = await _seed_provider(tenant_id, user_id)
    custom = await _seed_model(provider.id, "my-custom-model", ["text"])
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "default")
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "ocr")
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "transcription")

    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    assert outcome.assigned_chat_model is None
    assert outcome.assigned_vision_model is None
    assert outcome.assigned_stt_model is None
    for task_type in ("default", "ocr", "transcription"):
        slot = await slot_of(tenant_id, user_id, task_type)
        assert slot.model_id == custom.id


# ---------------------------------------------------------------------------
# case 6 — empty/dead slots gap-fill
# ---------------------------------------------------------------------------


async def test_case_6_empty_and_dead_slots_rebind(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"]))
    provider = await _seed_provider(tenant_id, user_id)
    doomed = await _seed_model(provider.id, "old-stt", ["stt"])
    # deleting the model nulls the FK (ON DELETE SET NULL) — the "dead id"
    # shape on Postgres (study's SQLite PRAGMA trick has no equivalent).
    await _seed_slot(tenant_id, user_id, provider.id, doomed.id, "transcription")
    async with AsyncSessionLocal() as session:
        await session.delete(await session.get(AIModel, doomed.id))
        await session.commit()

    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    _providers, models = await user_rows(tenant_id)
    by_wire = {m.model_name: m for m in models}
    assert outcome.assigned_stt_model == "whisper-1"
    stt_slot = await slot_of(tenant_id, user_id, "transcription")
    assert stt_slot.model_id == by_wire["whisper-1"].id
    # chat slot was empty (no row) → bound
    assert outcome.assigned_chat_model == "gpt-5.6-terra"


# ---------------------------------------------------------------------------
# case 7 — invalid key persists nothing
# ---------------------------------------------------------------------------


def _http_status_error(status: int, message: str = "err") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x.test")
    response = httpx.Response(status, request=request, text=message)
    return httpx.HTTPStatusError(message, request=request, response=response)


async def test_case_7_invalid_key_persists_nothing(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]

    async def boom(wire_type, base_url, api_key, transport=None):
        raise _http_status_error(401, "Incorrect API key")

    monkeypatch.setattr(byok_setup, "fetch_remote_models", boom)

    providers_before, models_before = await user_rows(tenant_id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as excinfo:
            await byok_setup.setup_provider_from_preset(
                db, "openai", "sk-bad", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
            )
    assert excinfo.value.classified.code.value == "invalid_key"

    providers_after, models_after = await user_rows(tenant_id)
    assert len(providers_after) == len(providers_before)
    assert len(models_after) == len(models_before)
    assert await slot_of(tenant_id, user_id, "default") is None


# ---------------------------------------------------------------------------
# case 8 — unknown preset
# ---------------------------------------------------------------------------


async def test_case_8_unknown_preset_typed_error_without_fetch(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    calls = install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra"]))
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.UnknownPresetError):
            await byok_setup.setup_provider_from_preset(
                db,
                "not-a-preset",
                "sk-key",
                scope=AIScope.USER,
                tenant_id=tenant_id,
                user_id=user_id,
            )
    assert calls == []


# ---------------------------------------------------------------------------
# case 9 — timeout + local_not_running
# ---------------------------------------------------------------------------


async def test_case_9_hanging_fetch_times_out_and_local_refused_is_local_not_running(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, httpx.ReadTimeout("timed out"))
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as timeout_error:
            await byok_setup.setup_provider_from_preset(
                db, "openai", "sk-key", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
            )
    assert timeout_error.value.classified.code.value == "timeout"

    install_catalog(monkeypatch, httpx.ConnectError("connection refused"))
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as refused_error:
            await byok_setup.setup_provider_from_preset(
                db, "ollama", "", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
            )
    assert refused_error.value.classified.code.value == "local_not_running"


# ---------------------------------------------------------------------------
# case 10 — classifier table + suspectedVendor hints
# ---------------------------------------------------------------------------


def test_case_10_classifier_table_and_suspectedVendor_hints() -> None:
    def code(error: Exception, **kwargs: Any) -> str:
        return classify_provider_error(error, local_provider=False, **kwargs).code.value

    assert code(_http_status_error(401)) == "invalid_key"
    assert code(_http_status_error(402)) == "insufficient_credit"
    assert (
        code(_http_status_error(400, "failed with status 400: insufficient_quota"))
        == "insufficient_credit"
    )
    assert code(_http_status_error(429)) == "new_user_quota"
    assert (
        code(_http_status_error(403, "ORGANIZATION_RESTRICTED: region not supported"))
        == "region_unavailable"
    )
    assert code(_http_status_error(403, "forbidden")) == "unknown"
    assert code(httpx.ReadTimeout("aborted")) == "timeout"
    assert (
        classify_provider_error(httpx.ConnectError("ECONNREFUSED"), local_provider=True).code.value
        == "local_not_running"
    )
    assert code(httpx.ConnectError("fetch failed")) == "unknown"

    mis_pasted = classify_provider_error(
        _http_status_error(401),
        local_provider=False,
        api_key="sk-or-v1-abc",
        attempted_preset="openai",
    )
    assert mis_pasted.code.value == "invalid_key"
    assert mis_pasted.suspected_vendor == "openrouter"
    assert (
        classify_provider_error(
            _http_status_error(401),
            local_provider=False,
            api_key="sk-ant-api03-x",
            attempted_preset="openai",
        ).suspected_vendor
        == "anthropic"
    )
    assert (
        classify_provider_error(
            _http_status_error(401),
            local_provider=False,
            api_key="sk-abc123",
            attempted_preset="openai",
        ).suspected_vendor
        is None
    )
    assert extract_error_status("API request failed with status 401: bad key") == 401
    assert extract_error_status("fetch failed") is None


# ---------------------------------------------------------------------------
# case 11 — snapshot-suffix curation + curated_missed fallback
# ---------------------------------------------------------------------------


async def test_case_11_snapshot_suffix_curation_and_zero_match_fallback(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra-2026-09-11", "gpt-oss-120b"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    assert outcome.curated_missed is False
    assert outcome.catalog_count == 1
    assert outcome.assigned_chat_model == "gpt-5.6-terra-2026-09-11"

    install_catalog(monkeypatch, wire_catalog(["gpt-99-turbo", "gpt-99-mini"]))
    async with AsyncSessionLocal() as db:
        drifted = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )

    assert drifted.curated_missed is True
    assert drifted.catalog_count == 2
    assert drifted.assigned_chat_model is None


# ---------------------------------------------------------------------------
# case 12 — capability inference split
# ---------------------------------------------------------------------------


def test_case_12_capability_inference_split() -> None:
    assert infer_caps("whisper-1") == ["stt"]
    assert infer_caps("whisper-large-v3") == ["stt"]
    assert infer_caps("tts-1") == ["tts"]
    assert infer_caps("tts-1-hd") == ["tts"]
    assert infer_caps("text-embedding-3-small") == ["embeddings"]
    assert infer_caps("gpt-5.6-terra") == ["text", "vision", "tools"]
    assert infer_caps("llama3.3") == ["text"]


# ---------------------------------------------------------------------------
# case 13 — set-default guards (endpoint level)
# ---------------------------------------------------------------------------


async def test_case_13_set_default_task_scoping_guards_and_cross_provider_rejection(
    async_client, user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = user_ctx["headers"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "text-only"]))
    setup = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-test"},
        headers=headers,
    )
    assert setup.status_code == 200, setup.text
    provider_id = setup.json()["provider"]["id"]

    # capability guard: a text-only model cannot serve the vision (ocr) slot
    guard = await async_client.put(
        f"/api/v1/ai-config/providers/{provider_id}/set-default",
        json={"model_name": "text-only", "task": "ocr"},
        headers=headers,
    )
    assert guard.status_code == 422, guard.text

    # cross-provider model id → 409
    other = await _seed_provider(
        user_ctx["tenant_id"],
        user_ctx["user_id"],
        name="Other",
        api_base="https://other.test/v1",
    )
    await _seed_model(other.id, "shared-model", ["text"])
    cross = await async_client.put(
        f"/api/v1/ai-config/providers/{provider_id}/set-default",
        json={"model_name": "shared-model"},
        headers=headers,
    )
    assert cross.status_code == 409, cross.text

    # unknown model id → created as a manual row, then bound
    custom = await async_client.put(
        f"/api/v1/ai-config/providers/{provider_id}/set-default",
        json={"model_name": "brand-new-model"},
        headers=headers,
    )
    assert custom.status_code == 200, custom.text
    slot = await slot_of(user_ctx["tenant_id"], user_ctx["user_id"], "default")
    async with AsyncSessionLocal() as session:
        model = await session.get(AIModel, slot.model_id)
    assert model.model_name == "brand-new-model"


# ---------------------------------------------------------------------------
# case 14 — USER-scope isolation (health addition)
# ---------------------------------------------------------------------------


async def test_case_14_setup_never_clobbers_system_tenant_or_other_users(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.database import AsyncSessionLocal as SessionLocal

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    other_tenant = uuid.uuid4()
    other_user = uuid.uuid4()
    async with SessionLocal() as session:
        session.add(
            TenantModel(
                id=other_tenant,
                name="Other Tenant",
                slug=f"byok-other-{other_tenant}",
            )
        )
        await session.commit()

    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"]))

    # a SYSTEM assignment (admin's global default)
    sys_provider = AIProviderModel(
        name="System row",
        scope=AIScope.SYSTEM,
        provider_type="openai",
        api_base="https://api.openai.com/v1",
        is_active=True,
        settings={},
    )
    async with SessionLocal() as session:
        session.add(sys_provider)
        await session.commit()
        await session.refresh(sys_provider)
        sys_model = AIModel(
            provider_id=sys_provider.id,
            name="system-model",
            model_name="system-model",
            capabilities=["text"],
            is_active=True,
            settings={},
        )
        session.add(sys_model)
        await session.commit()
        await session.refresh(sys_model)
        sys_slot = AITaskAssignment(
            task_type="default",
            scope=AIScope.SYSTEM,
            provider_id=sys_provider.id,
            model_id=sys_model.id,
            is_active=True,
            priority=0,
        )
        tenant_slot = AITaskAssignment(
            task_type="default",
            scope=AIScope.TENANT,
            tenant_id=tenant_id,
            provider_id=sys_provider.id,
            model_id=sys_model.id,
            is_active=True,
            priority=0,
        )
        session.add(sys_slot)
        session.add(tenant_slot)
        await session.commit()

    # another user's personal setup in the same tenant
    async with AsyncSessionLocal() as db:
        await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-other", scope=AIScope.USER, tenant_id=tenant_id, user_id=other_user
        )

    # OUR setup
    async with AsyncSessionLocal() as db:
        ours = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-ours", scope=AIScope.USER, tenant_id=tenant_id, user_id=user_id
        )
    assert ours.assigned_chat_model == "gpt-5.6-terra"

    async with SessionLocal() as session:
        sys_slot_after = (
            (
                await session.execute(
                    select(AITaskAssignment).where(
                        AITaskAssignment.scope == AIScope.SYSTEM,
                        AITaskAssignment.task_type == "default",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sys_slot_after) == 1
        assert sys_slot_after[0].model_id == sys_model.id

        tenant_slots_after = (
            (
                await session.execute(
                    select(AITaskAssignment).where(
                        AITaskAssignment.scope == AIScope.TENANT,
                        AITaskAssignment.tenant_id == tenant_id,
                        AITaskAssignment.task_type == "default",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(tenant_slots_after) == 1
        assert tenant_slots_after[0].model_id == sys_model.id

        other_slots = (
            (
                await session.execute(
                    select(AITaskAssignment).where(
                        AITaskAssignment.scope == AIScope.USER,
                        AITaskAssignment.user_id == other_user,
                        AITaskAssignment.task_type == "default",
                        AITaskAssignment.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(other_slots) == 1
        assert other_slots[0].provider_id != ours.provider_id

        # adoption never crosses users: ours is a NEW row, not the other user's
        providers = (
            (
                await session.execute(
                    select(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert {p.user_id for p in providers} == {user_id, other_user}

        # cleanup system rows
        await session.execute(
            delete(AITaskAssignment).where(AITaskAssignment.provider_id == sys_provider.id)
        )
        await session.delete(sys_model)
        await session.delete(sys_provider)
        await session.execute(delete(TenantModel).where(TenantModel.id == other_tenant))
        await session.commit()


# ---------------------------------------------------------------------------
# options body + endpoint error shape
# ---------------------------------------------------------------------------


async def test_setup_options_are_honored_and_use_contract_names(
    async_client, user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = byok_setup.SetupOptions()
    assert hasattr(options, "curated_ids")
    assert hasattr(options, "bind_chat")
    assert hasattr(options, "bind_vision")
    assert hasattr(options, "bind_stt")
    assert hasattr(options, "bind_decision")

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openai",
            "sk-key",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            options=byok_setup.SetupOptions(bind_chat=False, bind_stt=False),
        )
    assert outcome.assigned_chat_model is None
    assert outcome.assigned_stt_model is None
    assert outcome.assigned_vision_model == "gpt-5.6-terra"


async def test_setup_endpoint_maps_classified_errors_to_the_15_body(
    async_client, user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_catalog(monkeypatch, httpx.ReadTimeout("timed out"))
    response = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-key"},
        headers=user_ctx["headers"],
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "timeout"
    assert "suspected_vendor" in detail

    unknown = await async_client.post(
        "/api/v1/ai-config/providers/not-a-preset/setup",
        json={"api_key": "sk-key"},
        headers=user_ctx["headers"],
    )
    assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# scope-aware setup (SYSTEM / TENANT / USER)
# ---------------------------------------------------------------------------


async def test_scope_aware_setup_creates_rows_at_the_requested_scope(
    async_client, user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin system/tenant surfaces run the SAME one-click setup against
    their own config layer (recorded plan-17 Phase 3 delta: health's setup is
    scope-aware, not USER-only)."""
    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"]))

    sysadmin_headers = await headers_for_claims(
        {
            "sub": "sysadmin2@test.local",
            "tenant_id": str(user_ctx["tenant_id"]),
            "role": "SYSTEM_ADMIN",
        }
    )
    admin_headers = await headers_for_claims(
        {
            "sub": "tenantadmin@test.local",
            "tenant_id": str(user_ctx["tenant_id"]),
            "role": "ADMIN",
        }
    )

    # SYSTEM scope
    setup_system = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-sys", "scope": "SYSTEM"},
        headers=sysadmin_headers,
    )
    assert setup_system.status_code == 200, setup_system.text
    assert setup_system.json()["provider"]["scope"] == "SYSTEM"

    # TENANT scope (tenant admin)
    setup_tenant = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-tenant", "scope": "TENANT"},
        headers=admin_headers,
    )
    assert setup_tenant.status_code == 200, setup_tenant.text
    tenant_provider = setup_tenant.json()["provider"]
    assert tenant_provider["scope"] == "TENANT"
    assert tenant_provider["tenant_id"] == str(user_ctx["tenant_id"])

    # USER scope (default) — untouched semantics
    setup_user = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-user"},
        headers=user_ctx["headers"],
    )
    assert setup_user.status_code == 200, setup_user.text
    assert setup_user.json()["provider"]["scope"] == "USER"

    async with AsyncSessionLocal() as session:
        (
            (
                await session.execute(
                    select(AIProviderModel).where(
                        AIProviderModel.preset_key == "openai",
                        AITaskAssignment.__table__ is not None,
                    )
                )
            )
            .scalars()
            .all()
        )

        slots = (
            (
                await session.execute(
                    select(AITaskAssignment).where(
                        AITaskAssignment.task_type == "default",
                        AITaskAssignment.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(s.scope.value for s in slots) == ["SYSTEM", "TENANT", "USER"]

        # cleanup SYSTEM + TENANT rows created here (user_ctx cleans its own)
        await session.execute(
            delete(AITaskAssignment).where(AITaskAssignment.scope != AIScope.USER)
        )
        await session.execute(delete(AIProviderModel).where(AIProviderModel.scope != AIScope.USER))
        await session.commit()

    # role guard: a plain USER cannot create SYSTEM/TENANT rows
    forbidden = await async_client.post(
        "/api/v1/ai-config/providers/openai/setup",
        json={"api_key": "sk-x", "scope": "SYSTEM"},
        headers=user_ctx["headers"],
    )
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# plan 26 / ADR-0032 - decision cases (15-22) + the D17 PHI scope guard (23)
# ---------------------------------------------------------------------------

HEALTH_DECISION_SLOTS = [
    "decision_tool_dispatch",
    "decision_boolean_gate",
    "decision_choice",
    "decision_score",
]


def install_probe(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
) -> list[tuple[str, str, str]]:
    from app.ai.decide.systemone.probe import DecisionProbeInput

    calls: list[tuple[str, str, str]] = []

    def fake_probe(probe_input: DecisionProbeInput):
        calls.append(
            (
                probe_input.provider.preset_key or "",
                probe_input.model.model_name,
                probe_input.api_key,
            )
        )
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(byok_setup, "probe_decision_provider", fake_probe)
    return calls


async def _seed_live_decision_slot(
    tenant_id, user_id, task_type: str
) -> tuple[AIProviderModel, AIModel, AITaskAssignment]:
    """A decision model bound to ``task_type`` from a DIFFERENT provider."""
    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name="Other TypeSafe",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_type="decision",
            api_base="https://api.typesafe.ai",
            api_key=None,
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name="pinned-jev",
            model_name="pinned-jev",
            capabilities=["decision"],
            decision_transport="systemone",
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.flush()
        row = AITaskAssignment(
            task_type=task_type,
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            provider_id=provider.id,
            model_id=model.id,
            is_active=True,
            priority=0,
        )
        session.add(row)
        await session.commit()
        await session.refresh(provider)
        await session.refresh(model)
        await session.refresh(row)
        return provider, model, row


async def test_case_15_bind_decision_gapfills_declared_slots_scope_local(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["openrouter/auto"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openrouter",
            "sk-or-v1-key",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert outcome.assigned_chat_model == "openrouter/auto"
    assert outcome.assigned_decision_slots is not None
    assert set(outcome.assigned_decision_slots) == set(HEALTH_DECISION_SLOTS)
    assert set(outcome.assigned_decision_slots.values()) == {"openrouter.jev-latest"}
    assert outcome.catalog_count == 2
    async with AsyncSessionLocal() as session:
        decision_models = (
            (
                await session.execute(
                    select(AIModel).where(AIModel.model_name == "openrouter.jev-latest")
                )
            )
            .scalars()
            .all()
        )
        assert len(decision_models) == 1
        assert decision_models[0].get_capabilities() == ["decision"]
        assert decision_models[0].decision_transport == "systemone"
        for slot in HEALTH_DECISION_SLOTS:
            row = await slot_of(tenant_id, user_id, slot)
            assert row is not None
            assert str(row.model_id) == str(decision_models[0].id)
            assert row.provider_id == decision_models[0].provider_id
            assert row.scope == AIScope.USER
            assert row.phi_allowed is False


async def test_case_16_bind_decision_off_persists_models_binds_nothing(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(monkeypatch, wire_catalog(["openrouter/auto"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openrouter",
            "sk-or-v1-key",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
            options=byok_setup.SetupOptions(bind_decision=False),
        )

    assert outcome.assigned_decision_slots is None
    assert outcome.assigned_chat_model == "openrouter/auto"
    async with AsyncSessionLocal() as session:
        decision_models = (
            (
                await session.execute(
                    select(AIModel).where(AIModel.model_name == "openrouter.jev-latest")
                )
            )
            .scalars()
            .all()
        )
        assert len(decision_models) == 1
    for slot in HEALTH_DECISION_SLOTS:
        assert await slot_of(tenant_id, user_id, slot) is None


async def test_case_17_bind_decision_never_clobbers_live_slots(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    _p, live_model, live_row = await _seed_live_decision_slot(
        tenant_id, user_id, "decision_tool_dispatch"
    )
    install_catalog(monkeypatch, wire_catalog(["openrouter/auto"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openrouter",
            "sk-or-v1-b",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert outcome.assigned_chat_model == "openrouter/auto"
    assert outcome.assigned_decision_slots is not None
    assert "decision_tool_dispatch" not in outcome.assigned_decision_slots
    assert set(outcome.assigned_decision_slots) == {
        "decision_boolean_gate",
        "decision_choice",
        "decision_score",
    }
    async with AsyncSessionLocal() as session:
        refreshed = (
            (
                await session.execute(
                    select(AITaskAssignment).where(AITaskAssignment.id == live_row.id)
                )
            )
            .scalars()
            .first()
        )
        assert refreshed is not None
        assert str(refreshed.model_id) == str(live_model.id)


async def test_case_18_decision_only_preset_probe_validates_and_persists(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.decide.systemone.probe import DecisionProbeResult

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    probe_calls = install_probe(
        monkeypatch,
        DecisionProbeResult(ok=True, latency_ms=5, resolved_model="jev-1.13"),
    )
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "typesafe",
            "  ts-key  ",
            scope=AIScope.USER,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert probe_calls == [("typesafe", "jev-latest", "ts-key")]
    assert outcome.assigned_chat_model is None
    assert outcome.assigned_decision_slots is not None
    assert set(outcome.assigned_decision_slots.values()) == {"jev-latest"}
    assert outcome.catalog_count == 1
    async with AsyncSessionLocal() as session:
        providers = (
            (
                await session.execute(
                    select(AIProviderModel).where(
                        AIProviderModel.tenant_id == tenant_id,
                        AIProviderModel.preset_key == "typesafe",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(providers) == 1
        provider = providers[0]
        assert provider.provider_type == "decision"
        assert provider.api_base == "https://api.typesafe.ai"
        assert provider.scope == AIScope.USER
        models = (
            (await session.execute(select(AIModel).where(AIModel.provider_id == provider.id)))
            .scalars()
            .all()
        )
        assert [m.model_name for m in models] == ["jev-latest"]
        assert models[0].get_capabilities() == ["decision"]
        assert models[0].decision_transport == "systemone"
        from app.core.encryption import decrypt_secret

        assert decrypt_secret(provider.api_key) == "ts-key"


async def test_case_18_decision_only_probe_failure_persists_nothing(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.decide.systemone.probe import DecisionProbeResult

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_probe(
        monkeypatch,
        DecisionProbeResult(ok=False, latency_ms=5, status=401, error="bad key"),
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as excinfo:
            await byok_setup.setup_provider_from_preset(
                db,
                "typesafe",
                "ts-bad",
                scope=AIScope.USER,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    assert excinfo.value.classified.code.value == "invalid_key"
    providers, _models = await user_rows(tenant_id)
    assert providers == []


async def test_case_19_decision_only_probe_timeout_classifies_timeout(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.decide.systemone.probe import DecisionProbeResult

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_probe(
        monkeypatch,
        DecisionProbeResult(ok=False, latency_ms=10_000, kind="timeout", error="probe timed out"),
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as excinfo:
            await byok_setup.setup_provider_from_preset(
                db,
                "typesafe",
                "ts-key",
                scope=AIScope.USER,
                tenant_id=tenant_id,
                user_id=user_id,
            )

    assert excinfo.value.classified.code.value == "timeout"
    providers, _models = await user_rows(tenant_id)
    assert providers == []


async def test_case_20_probe_is_no_oracle_without_a_valid_key(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.decide.systemone.probe import DecisionProbeResult

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_probe(
        monkeypatch,
        DecisionProbeResult(ok=False, latency_ms=5, status=403, error="forbidden"),
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as forbidden:
            await byok_setup.setup_provider_from_preset(
                db,
                "typesafe",
                "not-a-real-key",
                scope=AIScope.USER,
                tenant_id=tenant_id,
                user_id=user_id,
            )
    assert forbidden.value.classified.code.value == "unknown"

    install_probe(
        monkeypatch,
        DecisionProbeResult(ok=False, latency_ms=5, status=402, error="no credit"),
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as credit:
            await byok_setup.setup_provider_from_preset(
                db,
                "typesafe",
                "not-a-real-key",
                scope=AIScope.USER,
                tenant_id=tenant_id,
                user_id=user_id,
            )
    assert credit.value.classified.code.value == "insufficient_credit"
    providers, _models = await user_rows(tenant_id)
    assert providers == []


async def test_case_21_slot_separation_enforced_both_directions(
    user_ctx,
) -> None:
    from app.ai.providers.capabilities import required_capabilities_for_task
    from app.models.enums import AIModelCapability

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    chat_provider = await _seed_provider(tenant_id, user_id, name="Chat")
    await _seed_model(chat_provider.id, "gpt-x", ["text", "vision"])
    decision_provider = await _seed_provider(
        tenant_id,
        user_id,
        name="TypeSafe",
        provider_type="decision",
        api_base="https://api.typesafe.ai",
    )
    decision_model = await _seed_model(decision_provider.id, "jev-latest", ["decision"])
    async with AsyncSessionLocal() as session:
        decision_model.decision_transport = "systemone"
        session.add(decision_model)
        await session.commit()

    assert required_capabilities_for_task("decision_tool_dispatch") == {AIModelCapability.DECISION}

    async with AsyncSessionLocal() as db:
        bound = await byok_setup.set_default_model(
            db, decision_provider, "jev-latest", task="decision_tool_dispatch"
        )
        assert bound.model_name == "jev-latest"
        with pytest.raises(ValueError, match="decision"):
            await byok_setup.set_default_model(db, chat_provider, "gpt-x", task="decision_choice")
        with pytest.raises(ValueError, match="text"):
            await byok_setup.set_default_model(db, decision_provider, "jev-latest", task="default")
        with pytest.raises(ValueError, match="unknown task"):
            await byok_setup.set_default_model(
                db, decision_provider, "jev-latest", task="decision_open_args"
            )


async def test_case_21_summary_never_falls_back_to_chat_default(user_ctx) -> None:
    """D4: the config summary resolves decision slots from their own rows —
    a bound ``default`` (chat) assignment never leaks into them."""
    from app.ai.providers.enums import TaskType
    from app.ai.providers.service import AIProviderService

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    chat_provider = await _seed_provider(tenant_id, user_id, name="Chat")
    chat_model = await _seed_model(chat_provider.id, "gpt-x", ["text"])
    await _seed_slot(tenant_id, user_id, chat_provider.id, chat_model.id, TaskType.DEFAULT.value)

    async with AsyncSessionLocal() as db:
        summary = await AIProviderService(db).get_config_summary(
            tenant_id=tenant_id, user_id=user_id, scope=AIScope.USER
        )
    for slot in HEALTH_DECISION_SLOTS:
        assert getattr(summary, slot) is None
    assert summary.default is not None


def test_case_22_preset_catalog_decision_data_shape() -> None:
    from app.ai.providers.presets import (
        ALL_PRESETS,
        PRESET_ORDER,
        SETUP_PRESETS,
        decision_models_for_preset,
    )

    assert "typesafe" in ALL_PRESETS
    assert "typesafe" not in PRESET_ORDER
    assert "typesafe" not in SETUP_PRESETS
    assert ALL_PRESETS["typesafe"]["type"] == "decision"
    assert ALL_PRESETS["typesafe"]["provisional"] is True
    assert ALL_PRESETS["typesafe"]["decision_default"] == "jev-latest"
    openrouter = ALL_PRESETS["openrouter"]
    assert openrouter["decision_default"] == "openrouter.jev-latest"
    assert openrouter["decision_models"][0]["provisional"] is True
    assert decision_models_for_preset("openai") == []
    for preset in ALL_PRESETS.values():
        for entry in preset["decision_models"] or []:
            assert set(entry["caps"]) <= {
                "tool-dispatch",
                "boolean-gate",
                "choice",
                "score",
            }


async def test_case_23_phi_scope_guard_explicit_opt_in_only(async_client, user_ctx) -> None:
    """D17: assignments are PHI-excluded by default; the opt-in exists on
    decision_* task types only, and only via the explicit assignment API."""
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    decision_provider = await _seed_provider(
        tenant_id,
        user_id,
        name="TypeSafe",
        provider_type="decision",
        api_base="https://api.typesafe.ai",
    )
    model = await _seed_model(decision_provider.id, "jev-latest", ["decision"])

    rejected = await async_client.post(
        "/api/v1/ai-config/task-assignments",
        json={
            "task_type": "chat",
            "scope": "USER",
            "provider_id": str(decision_provider.id),
            "model_id": str(model.id),
            "phi_allowed": True,
        },
        headers=user_ctx["headers"],
    )
    assert rejected.status_code == 422

    created = await async_client.post(
        "/api/v1/ai-config/task-assignments",
        json={
            "task_type": "decision_tool_dispatch",
            "scope": "USER",
            "provider_id": str(decision_provider.id),
            "model_id": str(model.id),
            "phi_allowed": True,
        },
        headers=user_ctx["headers"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["phi_allowed"] is True

    plain = await async_client.post(
        "/api/v1/ai-config/task-assignments",
        json={
            "task_type": "decision_score",
            "scope": "USER",
            "provider_id": str(decision_provider.id),
            "model_id": str(model.id),
        },
        headers=user_ctx["headers"],
    )
    assert plain.status_code == 201, plain.text
    assert plain.json()["phi_allowed"] is False

    revoked = await async_client.put(
        f"/api/v1/ai-config/task-assignments/{created.json()['id']}",
        json={"phi_allowed": False},
        headers=user_ctx["headers"],
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["phi_allowed"] is False

    # Smuggling the opt-in onto a NON-decision task type is a 422 — the
    # guard is decision-specific by contract.
    chat_provider = await _seed_provider(tenant_id, user_id, name="Chat")
    chat_model = await _seed_model(chat_provider.id, "gpt-x", ["text"])
    chat_assignment = await async_client.post(
        "/api/v1/ai-config/task-assignments",
        json={
            "task_type": "default",
            "scope": "USER",
            "provider_id": str(chat_provider.id),
            "model_id": str(chat_model.id),
        },
        headers=user_ctx["headers"],
    )
    assert chat_assignment.status_code == 201, chat_assignment.text
    smuggled = await async_client.put(
        f"/api/v1/ai-config/task-assignments/{chat_assignment.json()['id']}",
        json={"phi_allowed": True},
        headers=user_ctx["headers"],
    )
    assert smuggled.status_code == 422

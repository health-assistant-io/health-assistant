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
"""

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.tenant_model import TenantModel
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
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AITaskAssignment,
    AIScope,
)


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_ctx():
    """A real tenant + USER-role JWT + a raw id pair for direct assertions."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(
                id=tenant_id, name="BYOK Tenant", slug=f"byok-{tenant_id}"
            )
        )
        await session.commit()

    token = create_access_token(
        {
            "sub": "byok@test.local",
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": "USER",
        }
    )
    ctx = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    yield ctx

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AITaskAssignment).where(AITaskAssignment.tenant_id == tenant_id)
        )
        await session.execute(
            delete(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id)
        )
        await session.commit()


async def _seed_provider(
    tenant_id, user_id, *, name="Manual row", provider_type="openai",
    api_base="https://api.openai.com/v1", preset_key=None,
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


async def _seed_model(
    provider_id, model_name, caps
) -> AIModel:
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


async def _seed_slot(
    tenant_id, user_id, provider_id, model_id, task_type
) -> AITaskAssignment:
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
    return [
        byok_setup.RemoteModel(external_id=m, caps=tuple(infer_caps(m)))
        for m in model_ids
    ]


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
            await session.execute(
                select(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id)
            )
        ).scalars().all()
        models = (
            (await session.execute(select(AIModel))).scalars().all()
        )
    return list(providers), list(models)


async def slot_of(tenant_id, user_id, task_type) -> AITaskAssignment | None:
    async with AsyncSessionLocal() as session:
        return (
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
        ).scalars().first()


# ---------------------------------------------------------------------------
# case 2 — preset metadata uniformity (health overlay)
# ---------------------------------------------------------------------------


def test_preset_metadata_uniformity() -> None:
    assert list(SETUP_PRESETS) == [
        k for k in PRESET_ORDER if k not in DISABLED_PRESET_REASONS
    ]
    assert sorted(SETUP_PRESETS) == [
        "deepseek", "groq", "mistral", "ollama", "openai", "openrouter",
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
        "sk-ant-", "sk-or-v1-", "gsk_", "AIza", "sk-",
    ]
    assert guess_preset_for_key("sk-ant-api03-xyz") == "anthropic"
    assert guess_preset_for_key("gsk_abc") == "groq"
    assert guess_preset_for_key("totally-random") is None
    assert guess_preset_for_key("") is None


# ---------------------------------------------------------------------------
# case 1 — happy path
# ---------------------------------------------------------------------------


async def test_case_1_happy_path_bindings(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            tenant_id=tenant_id,
            user_id=user_id,
        )

    assert outcome.catalog_count == 3
    assert outcome.assigned_chat_model == "gpt-5.6-terra"
    assert outcome.assigned_vision_model == "gpt-5.6-terra"
    assert outcome.assigned_stt_model == "whisper-1"
    assert calls == [
        ("openai_compatible", "https://api.openai.com/v1", "sk-live-key")
    ]

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


async def test_case_1_happy_path_ollama_keyless(
    user_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    calls = install_catalog(monkeypatch, wire_catalog(["llama3.3"]))
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "ollama", "", tenant_id=tenant_id, user_id=user_id
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
            db, "openai", "sk-first", tenant_id=tenant_id, user_id=user_id
        )
    manual_model = await _seed_model(first.provider_id, "old-favorite", ["text"])

    install_catalog(monkeypatch, wire_catalog(["gpt-5.6-terra", "gpt-5.6-sol"]))
    async with AsyncSessionLocal() as db:
        await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-second", tenant_id=tenant_id, user_id=user_id
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
            db, "openai", "sk-new", tenant_id=tenant_id, user_id=user_id
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
    install_catalog(
        monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"])
    )
    provider = await _seed_provider(tenant_id, user_id)
    custom = await _seed_model(provider.id, "my-custom-model", ["text"])
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "default")
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "ocr")
    await _seed_slot(tenant_id, user_id, provider.id, custom.id, "transcription")

    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", tenant_id=tenant_id, user_id=user_id
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
        await session.delete(
            await session.get(AIModel, doomed.id)
        )
        await session.commit()

    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", tenant_id=tenant_id, user_id=user_id
        )

    providers, models = await user_rows(tenant_id)
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
                db, "openai", "sk-bad", tenant_id=tenant_id, user_id=user_id
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
                db, "not-a-preset", "sk-key", tenant_id=tenant_id, user_id=user_id
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
                db, "openai", "sk-key", tenant_id=tenant_id, user_id=user_id
            )
    assert timeout_error.value.classified.code.value == "timeout"

    install_catalog(monkeypatch, httpx.ConnectError("connection refused"))
    async with AsyncSessionLocal() as db:
        with pytest.raises(byok_setup.SetupError) as refused_error:
            await byok_setup.setup_provider_from_preset(
                db, "ollama", "", tenant_id=tenant_id, user_id=user_id
            )
    assert refused_error.value.classified.code.value == "local_not_running"


# ---------------------------------------------------------------------------
# case 10 — classifier table + suspectedVendor hints
# ---------------------------------------------------------------------------


def test_case_10_classifier_table_and_suspectedVendor_hints() -> None:
    def code(error: Exception, **kwargs: Any) -> str:
        return classify_provider_error(
            error, local_provider=False, **kwargs
        ).code.value

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
        classify_provider_error(
            httpx.ConnectError("ECONNREFUSED"), local_provider=True
        ).code.value
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
    install_catalog(
        monkeypatch, wire_catalog(["gpt-5.6-terra-2026-09-11", "gpt-oss-120b"])
    )
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", tenant_id=tenant_id, user_id=user_id
        )

    assert outcome.curated_missed is False
    assert outcome.catalog_count == 1
    assert outcome.assigned_chat_model == "gpt-5.6-terra-2026-09-11"

    install_catalog(monkeypatch, wire_catalog(["gpt-99-turbo", "gpt-99-mini"]))
    async with AsyncSessionLocal() as db:
        drifted = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-key", tenant_id=tenant_id, user_id=user_id
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
    install_catalog(
        monkeypatch, wire_catalog(["gpt-5.6-terra", "text-only"])
    )
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
        user_ctx["tenant_id"], user_ctx["user_id"],
        name="Other", api_base="https://other.test/v1",
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
            task_type="default", scope=AIScope.SYSTEM, provider_id=sys_provider.id,
            model_id=sys_model.id, is_active=True, priority=0,
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
            db, "openai", "sk-other", tenant_id=tenant_id, user_id=other_user
        )

    # OUR setup
    async with AsyncSessionLocal() as db:
        ours = await byok_setup.setup_provider_from_preset(
            db, "openai", "sk-ours", tenant_id=tenant_id, user_id=user_id
        )
    assert ours.assigned_chat_model == "gpt-5.6-terra"

    async with SessionLocal() as session:
        sys_slot_after = (
            await session.execute(
                select(AITaskAssignment).where(
                    AITaskAssignment.scope == AIScope.SYSTEM,
                    AITaskAssignment.task_type == "default",
                )
            )
        ).scalars().all()
        assert len(sys_slot_after) == 1
        assert sys_slot_after[0].model_id == sys_model.id

        tenant_slots_after = (
            await session.execute(
                select(AITaskAssignment).where(
                    AITaskAssignment.scope == AIScope.TENANT,
                    AITaskAssignment.tenant_id == tenant_id,
                    AITaskAssignment.task_type == "default",
                )
            )
        ).scalars().all()
        assert len(tenant_slots_after) == 1
        assert tenant_slots_after[0].model_id == sys_model.id

        other_slots = (
            await session.execute(
                select(AITaskAssignment).where(
                    AITaskAssignment.scope == AIScope.USER,
                    AITaskAssignment.user_id == other_user,
                    AITaskAssignment.task_type == "default",
                    AITaskAssignment.is_active.is_(True),
                )
            )
        ).scalars().all()
        assert len(other_slots) == 1
        assert other_slots[0].provider_id != ours.provider_id

        # adoption never crosses users: ours is a NEW row, not the other user's
        providers = (
            await session.execute(
                select(AIProviderModel).where(
                    AIProviderModel.tenant_id == tenant_id
                )
            )
        ).scalars().all()
        assert {p.user_id for p in providers} == {user_id, other_user}

        # cleanup system rows
        await session.execute(
            delete(AITaskAssignment).where(
                AITaskAssignment.provider_id == sys_provider.id
            )
        )
        await session.delete(sys_model)
        await session.delete(sys_provider)
        await session.execute(
            delete(TenantModel).where(TenantModel.id == other_tenant)
        )
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

    tenant_id, user_id = user_ctx["tenant_id"], user_ctx["user_id"]
    install_catalog(
        monkeypatch, wire_catalog(["gpt-5.6-terra", "whisper-1"])
    )
    async with AsyncSessionLocal() as db:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            "openai",
            "sk-key",
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

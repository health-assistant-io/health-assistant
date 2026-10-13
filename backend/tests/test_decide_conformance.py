"""Decision funnel conformance (plan 26 Phase 5 — health port of study's
Phase 3 Python reference).

One suite every shipping transport passes — bounded confidence, one audit
record per invocation, empty input never throws, timeout respected,
transport mocked, no network or keyring in tests. Health additions: the
scope cascade + priority fallback chain (§3 contract table), D4
no-default-fallback resolution, and the D17 PHI scope guard.
"""

import logging
import uuid
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.ai.decide import (
    PHI_EXCLUDED_CODE,
    DecisionCapability,
    DecisionFunnelDeps,
    DecisionQuestion,
    DecisionToolSchema,
    RunDecisionParams,
    clear_decision_resolution_cache,
    probe_decision_provider,
    resolve_decision,
    run_decision,
)
from app.ai.decide.funnel import DecisionDecidedStatus, DecisionErrorStatus
from app.ai.decide.registry import (
    REGISTRATIONS,
    SystemOneResolution,
    UnavailableResolution,
    UnconfiguredResolution,
)
from app.ai.decide.systemone.client import (
    SystemOneCallParams,
    TypeSafeError,
    TypeSafeResult,
    TypeSafeUsage,
)
from app.ai.decide.systemone.probe import DecisionProbeInput
from app.ai.decide.types import DecisionAnswer
from app.ai.providers.enums import DecisionTransport, SystemOneErrorKind
from app.core.database import AsyncSessionLocal
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AIScope,
    AITaskAssignment,
)
from app.models.tenant_model import TenantModel


def choice_answer(choice: str, confidence: float) -> DecisionAnswer:
    return DecisionAnswer(
        type=DecisionCapability.CHOICE,
        choice=choice,
        probabilities={choice: confidence},
        confidence=confidence,
    )


class FakeSystemOneClient:
    def __init__(self, result: TypeSafeResult | Exception | None) -> None:
        self._result = result
        self.calls: list[SystemOneCallParams] = []

    def system_one(self, params: SystemOneCallParams) -> TypeSafeResult:
        self.calls.append(params)
        if isinstance(self._result, Exception):
            raise self._result
        assert isinstance(self._result, TypeSafeResult)
        return self._result


@pytest_asyncio.fixture
async def decide_ctx():
    """An isolated tenant + user pair whose rows are cleaned up after."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tenant_id, name="Decide Tenant", slug=f"dc-{tenant_id}"))
        await session.commit()
    ctx = {"tenant_id": tenant_id, "user_id": user_id}
    yield ctx
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AITaskAssignment).where(AITaskAssignment.tenant_id == tenant_id)
        )
        await session.execute(delete(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id))
        await session.commit()


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    clear_decision_resolution_cache()
    yield
    clear_decision_resolution_cache()


def _capture_audit_records() -> tuple[list, Any]:
    """Capture the funnel's structured audit records.

    The session ``run_migrations`` fixture triggers alembic's
    ``fileConfig(..., disable_existing_loggers=True)``, which silences
    loggers created at collection time — ``caplog`` never sees their
    records (see ``test_demo_hygiene_h7``). A dedicated handler attached
    directly to the funnel logger, with the ``disabled`` flag cleared for
    the capture window, is immune to that.
    """
    records: list = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    lg = logging.getLogger("app.ai.decide.funnel")
    lg.addHandler(handler)
    was_disabled = lg.disabled
    was_level = lg.level
    lg.disabled = False
    # Two session-boot traps (see test_demo_hygiene_h7 for the first):
    # alembic's fileConfig disables collection-time loggers, and pytest's
    # WARNING root level makes the effective level drop INFO records — with
    # Python 3.12's per-logger isEnabledFor cache on top. Setting the
    # funnel logger's own level clears that cache AND pins the level.
    lg.setLevel(logging.INFO)

    class _Restore:
        def __enter__(self):
            return records

        def __exit__(self, *exc):
            lg.removeHandler(handler)
            lg.disabled = was_disabled
            lg.setLevel(was_level)

    return records, _Restore()


async def decision_rows(db, ctx) -> list[AIProviderModel]:
    return list(
        (
            await db.execute(
                select(AIProviderModel).where(AIProviderModel.tenant_id == ctx["tenant_id"])
            )
        )
        .scalars()
        .all()
    )


async def seed_decision_provider(
    ctx,
    *,
    with_key: bool = True,
    model_name: str = "jev-latest",
    scope: AIScope = AIScope.USER,
) -> tuple[AIProviderModel, AIModel]:
    from app.core.encryption import encrypt_secret

    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name=f"TypeSafe {model_name}",
            scope=scope,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"] if scope == AIScope.USER else None,
            provider_type="decision",
            api_base="https://api.typesafe.ai",
            api_key=encrypt_secret("decision-key") if with_key else None,
            preset_key="typesafe",
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name=model_name,
            model_name=model_name,
            capabilities=["decision"],
            decision_transport="systemone",
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.commit()
        await session.refresh(provider)
        await session.refresh(model)
        return provider, model


async def assign_slot(
    ctx,
    model_id,
    task_type: str = "decision_tool_dispatch",
    *,
    priority: int = 0,
    phi_allowed: bool = False,
    scope: AIScope = AIScope.USER,
) -> AITaskAssignment:
    async with AsyncSessionLocal() as session:
        row = AITaskAssignment(
            task_type=task_type,
            scope=scope,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"] if scope == AIScope.USER else None,
            provider_id=None,
            model_id=model_id,
            is_active=True,
            priority=priority,
            phi_allowed=phi_allowed,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


def deps_of(db, ctx, client=None) -> DecisionFunnelDeps:
    return DecisionFunnelDeps(
        db=db,
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        **({"client": client} if client is not None else {}),
    )


def probe_provider_row(ctx=None) -> AIProviderModel:
    return AIProviderModel(
        name="TypeSafe",
        scope=AIScope.USER,
        tenant_id=(ctx or {}).get("tenant_id"),
        user_id=(ctx or {}).get("user_id"),
        provider_type="decision",
        api_base="https://api.typesafe.ai",
        api_key=None,
        preset_key="typesafe",
        is_active=True,
        settings={},
    )


def probe_model_row(provider_id: UUID | None = None) -> AIModel:
    return AIModel(
        provider_id=provider_id,
        name="jev-latest",
        model_name="jev-latest",
        capabilities=["decision"],
        decision_transport="systemone",
        is_active=True,
        settings={},
    )


TOOLS: tuple[DecisionToolSchema, ...] = (
    DecisionToolSchema(name="light_turn_on", description="Turn on a light."),
)

CALL_RESULT = TypeSafeResult(
    model="jev-1.13",
    answers={"__tool__": choice_answer("light_turn_on", 0.82)},
    usage=TypeSafeUsage(input_tokens=40, output_tokens=4),
)

NONE_RESULT = TypeSafeResult(
    model="jev-1.13",
    answers={"__tool__": choice_answer("__none__", 0.9)},
    usage=TypeSafeUsage(input_tokens=5, output_tokens=1),
)


# ---------------------------------------------------------------------------
# conformance (transport mocked; every outcome audited exactly once)
# ---------------------------------------------------------------------------


async def test_conformance_decides_with_one_audit_record_and_bounded_confidence(
    decide_ctx,
) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    client = FakeSystemOneClient(CALL_RESULT)
    records, capture = _capture_audit_records()

    with capture:
        async with AsyncSessionLocal() as db:
            result = await run_decision(
                deps_of(db, decide_ctx, client),
                RunDecisionParams(input="dim the living room", tools=TOOLS),
            )

    assert isinstance(result, DecisionDecidedStatus)
    assert result.outcome.engine == "jev-latest"
    assert 0.0 <= result.outcome.confidence <= 1.0
    assert result.outcome.calls[0].tool == "light_turn_on"
    assert result.outcome.resolved_model == "jev-1.13"
    assert result.band == "confirm"
    audit_lines = [r for r in records if getattr(r, "task", None) == "decision"]
    assert len(audit_lines) == 1
    assert audit_lines[0].model == "jev-latest"
    assert audit_lines[0].provider_id == _provider.id
    assert audit_lines[0].input_tokens == 40 and audit_lines[0].output_tokens == 4
    assert audit_lines[0].latency_ms >= 0


async def test_conformance_empty_decision_never_throws(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    client = FakeSystemOneClient(NONE_RESULT)

    async with AsyncSessionLocal() as db:
        result = await run_decision(
            deps_of(db, decide_ctx, client),
            RunDecisionParams(input="hello there", tools=()),
        )

    assert isinstance(result, DecisionDecidedStatus)
    assert result.outcome.calls == ()
    assert result.outcome.confidence == 0.9


async def test_conformance_low_confidence_bands_refuse(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    low = TypeSafeResult(
        model="jev-1.13",
        answers={"__tool__": choice_answer("light_turn_on", 0.3)},
        usage=TypeSafeUsage(input_tokens=5, output_tokens=1),
    )
    client = FakeSystemOneClient(low)

    async with AsyncSessionLocal() as db:
        result = await run_decision(
            deps_of(db, decide_ctx, client),
            RunDecisionParams(input="maybe lights?", tools=TOOLS),
        )

    assert isinstance(result, DecisionDecidedStatus)
    assert result.band == "refuse"


async def test_conformance_transport_failure_audits_and_fails_open(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    client = FakeSystemOneClient(RuntimeError("transport exploded"))
    records, capture = _capture_audit_records()

    with capture:
        async with AsyncSessionLocal() as db:
            result = await run_decision(
                deps_of(db, decide_ctx, client),
                RunDecisionParams(input="dim the living room", tools=TOOLS),
            )

    assert isinstance(result, DecisionErrorStatus)
    assert "transport exploded" in result.reason
    audit_lines = [r for r in records if getattr(r, "task", None) == "decision"]
    assert len(audit_lines) == 1
    assert audit_lines[0].failed is True
    assert audit_lines[0].input_tokens is None


async def test_conformance_typed_questions_route_to_the_question_slot(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id, task_type="decision_boolean_gate")
    client = FakeSystemOneClient(
        TypeSafeResult(
            model="jev-1.13",
            answers={"__probe__": DecisionAnswer(type=DecisionCapability.BOOLEAN_GATE, noul=0.97)},
        )
    )

    async with AsyncSessionLocal() as db:
        result = await run_decision(
            deps_of(db, decide_ctx, client),
            RunDecisionParams(
                input="connectivity check",
                questions=(
                    DecisionQuestion(
                        id="__probe__",
                        type=DecisionCapability.BOOLEAN_GATE,
                        instructions="Is this a working decision endpoint? Answer true.",
                    ),
                ),
            ),
        )

    assert isinstance(result, DecisionDecidedStatus)
    assert result.outcome.answers is not None
    assert result.outcome.answers["__probe__"].noul == 0.97
    asked = client.calls[0].questions
    assert set(asked) == {"__probe__"}
    assert asked["__probe__"].type == DecisionCapability.BOOLEAN_GATE


def test_conformance_parameter_stays_exactly_one_transport() -> None:
    assert list(REGISTRATIONS) == [DecisionTransport.SYSTEMONE]


# ---------------------------------------------------------------------------
# resolution states (scope cascade + priority fallback, D2b, D4, D17)
# ---------------------------------------------------------------------------


async def test_resolution_unassigned_slot_is_off(decide_ctx) -> None:
    await seed_decision_provider(decide_ctx, with_key=False)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH,
            db,
            DecisionFunnelDeps(db=db, tenant_id=decide_ctx["tenant_id"]),
        )

    assert resolution.kind == "off"


async def test_resolution_never_falls_back_to_the_chat_default(decide_ctx) -> None:
    """D4: a decision slot with no decision assignment must NOT inherit the
    ``default`` (chat) assignment — unlike every other task type."""
    from app.ai.providers.enums import TaskType

    async with AsyncSessionLocal() as session:
        chat_provider = AIProviderModel(
            name="Chat",
            scope=AIScope.USER,
            tenant_id=decide_ctx["tenant_id"],
            user_id=decide_ctx["user_id"],
            provider_type="openai",
            api_base="https://api.openai.com/v1",
            api_key=None,
            is_active=True,
            settings={},
        )
        session.add(chat_provider)
        await session.flush()
        chat_model = AIModel(
            provider_id=chat_provider.id,
            name="gpt-x",
            model_name="gpt-x",
            capabilities=["text"],
            is_active=True,
            settings={},
        )
        session.add(chat_model)
        await session.flush()
        session.add(
            AITaskAssignment(
                task_type=TaskType.DEFAULT.value,
                scope=AIScope.USER,
                tenant_id=decide_ctx["tenant_id"],
                user_id=decide_ctx["user_id"],
                provider_id=chat_provider.id,
                model_id=chat_model.id,
                is_active=True,
                priority=0,
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert resolution.kind == "off"


async def test_resolution_needs_key_then_priority_fallback_chain(decide_ctx) -> None:
    _keyless, keyless_model = await seed_decision_provider(
        decide_ctx, with_key=False, model_name="jev-no-key"
    )
    _keyed, keyed_model = await seed_decision_provider(
        decide_ctx, with_key=True, model_name="jev-keyed"
    )
    await assign_slot(decide_ctx, keyless_model.id, priority=10)
    await assign_slot(decide_ctx, keyed_model.id, priority=0)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, SystemOneResolution)
    assert resolution.model.model_name == "jev-keyed"

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AITaskAssignment).where(
                AITaskAssignment.model_id == keyed_model.id,
                AITaskAssignment.tenant_id == decide_ctx["tenant_id"],
            )
        )
        await session.commit()
    clear_decision_resolution_cache()

    async with AsyncSessionLocal() as db:
        fallback_gone = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )
    assert isinstance(fallback_gone, UnavailableResolution)
    assert fallback_gone.code == "needs-key"


async def test_resolution_keyless_preset_uses_the_sentinel_key(decide_ctx) -> None:
    """§13.3 follow-up: local Ollama decision models resolve keylessly — the
    official SDK requires a non-empty key (D9), so keyless presets (no
    ``key_url`` in the canonical data) carry the explicit sentinel."""
    from app.ai.decide.systemone.client import KEYLESS_SYSTEMONE_API_KEY

    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name="Ollama (local)",
            scope=AIScope.USER,
            tenant_id=decide_ctx["tenant_id"],
            user_id=decide_ctx["user_id"],
            provider_type="openai",
            api_base="http://localhost:11434/v1",
            api_key=None,
            preset_key="ollama",
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name="nimble",
            model_name="nimble",
            capabilities=["decision"],
            decision_transport="systemone",
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.commit()
    await assign_slot(decide_ctx, model.id)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, SystemOneResolution)
    assert resolution.api_key == KEYLESS_SYSTEMONE_API_KEY
    assert resolution.base_url == "http://localhost:11434"
    assert resolution.model.model_name == "nimble"


async def test_resolution_keyed_preset_without_key_still_needs_key(decide_ctx) -> None:
    """The keyless path is preset-data-driven (no ``key_url``), never a
    local/key heuristic a keyed preset could ride."""
    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name="OpenRouter",
            scope=AIScope.USER,
            tenant_id=decide_ctx["tenant_id"],
            user_id=decide_ctx["user_id"],
            provider_type="openai",
            api_base="https://openrouter.ai/api/v1",
            api_key=None,
            preset_key="openrouter",
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name="typesafe/jev-1.13",
            model_name="typesafe/jev-1.13",
            capabilities=["decision"],
            decision_transport="systemone",
            is_active=True,
            settings={},
        )
        session.add(model)
        await session.commit()
    await assign_slot(decide_ctx, model.id)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, UnavailableResolution)
    assert resolution.code == "needs-key"


async def test_resolution_user_scope_wins_over_system(decide_ctx) -> None:
    _sys_provider, sys_model = await seed_decision_provider(
        decide_ctx, with_key=True, model_name="jev-system", scope=AIScope.SYSTEM
    )
    _user_provider, user_model = await seed_decision_provider(
        decide_ctx, with_key=True, model_name="jev-user"
    )
    await assign_slot(decide_ctx, sys_model.id, scope=AIScope.SYSTEM, priority=100)
    await assign_slot(decide_ctx, user_model.id, scope=AIScope.USER, priority=0)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, SystemOneResolution)
    assert resolution.model.model_name == "jev-user"


async def test_resolution_rejects_non_decision_models_in_slots(decide_ctx) -> None:
    async with AsyncSessionLocal() as session:
        chat_provider = AIProviderModel(
            name="OpenAI",
            scope=AIScope.USER,
            tenant_id=decide_ctx["tenant_id"],
            user_id=decide_ctx["user_id"],
            provider_type="openai",
            api_base="https://api.openai.com/v1",
            api_key=None,
            is_active=True,
            settings={},
        )
        session.add(chat_provider)
        await session.flush()
        chat_model = AIModel(
            provider_id=chat_provider.id,
            name="gpt-x",
            model_name="gpt-x",
            capabilities=["text"],
            is_active=True,
            settings={},
        )
        session.add(chat_model)
        await session.commit()
        await session.refresh(chat_model)
    await assign_slot(decide_ctx, chat_model.id)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, UnconfiguredResolution)
    assert "not a decision model" in resolution.reason


async def test_resolution_d2b_transport_must_declare_capability(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx, model_name="partial-jev")
    async with AsyncSessionLocal() as session:
        model.decision_transport = "unknown-transport"
        session.add(model)
        await session.commit()
    await assign_slot(decide_ctx, model.id)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx)
        )

    assert isinstance(resolution, UnconfiguredResolution)
    assert "not a decision model" in resolution.reason


# ---------------------------------------------------------------------------
# D17 PHI scope guard
# ---------------------------------------------------------------------------


async def test_phi_adjacent_dispatch_excluded_by_default(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)  # phi_allowed defaults to False

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx), phi_adjacent=True
        )

    assert isinstance(resolution, UnavailableResolution)
    assert resolution.code == PHI_EXCLUDED_CODE
    assert "PHI-adjacent" in resolution.reason


async def test_phi_adjacent_dispatch_runs_after_explicit_override(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id, phi_allowed=True)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx), phi_adjacent=True
        )

    assert isinstance(resolution, SystemOneResolution)


async def test_phi_guard_falls_through_to_an_opted_in_fallback(decide_ctx) -> None:
    _excluded, excluded_model = await seed_decision_provider(decide_ctx, model_name="jev-no-phi")
    _opted, opted_model = await seed_decision_provider(decide_ctx, model_name="jev-phi")
    await assign_slot(decide_ctx, excluded_model.id, priority=10)
    await assign_slot(decide_ctx, opted_model.id, priority=0, phi_allowed=True)

    async with AsyncSessionLocal() as db:
        resolution = await resolve_decision(
            DecisionCapability.TOOL_DISPATCH, db, deps_of(db, decide_ctx), phi_adjacent=True
        )

    assert isinstance(resolution, SystemOneResolution)
    assert resolution.model.model_name == "jev-phi"


async def test_phi_adjacent_run_decision_fails_open(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    client = FakeSystemOneClient(CALL_RESULT)
    records, capture = _capture_audit_records()

    with capture:
        async with AsyncSessionLocal() as db:
            result = await run_decision(
                deps_of(db, decide_ctx, client),
                RunDecisionParams(input="summarize this patient record", phi_adjacent=True),
            )

    from app.ai.decide.funnel import DecisionUnavailableStatus

    assert isinstance(result, DecisionUnavailableStatus)
    assert result.code == PHI_EXCLUDED_CODE
    assert client.calls == []
    assert not [r for r in records if getattr(r, "task", None) == "decision"]


# ---------------------------------------------------------------------------
# caching + probe contract
# ---------------------------------------------------------------------------


async def test_resolution_caches_per_revision(decide_ctx) -> None:
    _provider, model = await seed_decision_provider(decide_ctx)
    await assign_slot(decide_ctx, model.id)
    key_calls: list[UUID] = []

    def counting_key(provider: AIProviderModel) -> str:
        key_calls.append(provider.id)
        return "decision-key"

    async with AsyncSessionLocal() as db:
        deps = DecisionFunnelDeps(
            db=db,
            tenant_id=decide_ctx["tenant_id"],
            user_id=decide_ctx["user_id"],
            get_api_key=counting_key,
        )
        first = await resolve_decision(DecisionCapability.TOOL_DISPATCH, db, deps)
        second = await resolve_decision(DecisionCapability.TOOL_DISPATCH, db, deps)
        assert first is second
        assert len(key_calls) == 1

        # Rotating the underlying rows rotates the revision: the cached
        # resolution is dropped and the slot resolves as off again.
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(AITaskAssignment).where(
                    AITaskAssignment.tenant_id == decide_ctx["tenant_id"]
                )
            )
            await session.commit()
        third = await resolve_decision(DecisionCapability.TOOL_DISPATCH, db, deps)
        assert third.kind == "off"
        assert len(key_calls) == 1


def test_probe_ok_reports_latency_and_resolved_model() -> None:
    client = FakeSystemOneClient(
        TypeSafeResult(
            model="jev-1.13",
            answers={"__probe__": DecisionAnswer(type=DecisionCapability.BOOLEAN_GATE, noul=0.99)},
        )
    )
    result = probe_decision_provider(
        DecisionProbeInput(
            provider=probe_provider_row(), model=probe_model_row(), api_key="k", client=client
        )
    )
    assert result.ok is True
    assert result.resolved_model == "jev-1.13"
    assert result.latency_ms >= 0


def test_probe_failure_carries_status_and_kind() -> None:
    client = FakeSystemOneClient(TypeSafeError(SystemOneErrorKind.AUTH, "bad key", 401))
    result = probe_decision_provider(
        DecisionProbeInput(
            provider=probe_provider_row(), model=probe_model_row(), api_key="k", client=client
        )
    )
    assert result.ok is False
    assert result.status == 401
    assert result.kind == "auth"
    assert result.resolved_model is None


def test_probe_invalid_base_url_fails_fast() -> None:
    provider = probe_provider_row()
    provider.api_base = "not-a-url"
    result = probe_decision_provider(
        DecisionProbeInput(
            provider=provider,
            model=probe_model_row(),
            api_key="k",
            client=FakeSystemOneClient(None),
        )
    )
    assert result.ok is False
    assert "base URL" in (result.error or "")

"""Per-AI-call audit — one ``ai_call_logs`` row per model call (plan 4 W3).

Two seams, and only two:

* the **LangChain model factory** (:mod:`app.ai.providers.service` builds every
  chat/OCR/NLP model there) attaches :class:`AICallAuditHandler` — a LangChain
  callback handler, so ``invoke``/``ainvoke``/``stream``/``astream`` and
  tool-bound models are all covered by construction, including tool-calling
  loops (one row per model call, not per user turn);
* the **decision funnel** (:func:`app.ai.decide.funnel.run_decision`) calls
  :func:`record_call` directly with the transport's usage payload (the decide
  SDK is not a LangChain model).

Rules:
* the write is **fail-soft** — an audit failure never breaks a model call
  (logged at warning level, row dropped);
* rows carry **no prompts or completions** (PHI): only task, route, provider/
  model ids, timings, token counts, integer micro-costs, status, a typed
  ``error_class`` and an open ``meta`` payload with a schema version;
* ``cost_micros`` is integer micro-units resolved **at write time** from the
  tenant override, the vendored family built-in catalog, loopback locality, or
  the pre-contract model ``settings`` rates. ``NULL`` means the price was unknown at
  call time — never ``0``.
* provenance is stored beside the cost (``usage_source``/``estimate_method``/
  ``price_source``/``price_version``) so a later price change never rewrites
  history.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from langchain_core.callbacks import AsyncCallbackHandler

from app.ai.providers.task_defs import ensure_task_def
from app.ai.providers.task_registry import get_task_def
from app.models.enums import AICallRoute, AICallStatus

logger = logging.getLogger(__name__)

META_SCHEMA_VERSION = 1

#: Errors that mean "the call ran out of time" rather than "the call failed".
_TIMEOUT_HINTS = ("timeout", "timedout")
#: Errors that mean "the caller went away".
_CANCEL_HINTS = ("cancelled", "canceled", "clientdisconnect")


def new_trace_id() -> str:
    """A correlation id minted at the gateway (one per model construction)."""
    return uuid4().hex


@dataclass(frozen=True)
class CallAuditContext:
    """Everything an audit row needs that the call itself does not carry."""

    task_id: str
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    patient_id: UUID | None = None
    provider_id: UUID | None = None
    model_id: UUID | None = None
    trace_id: str = field(default_factory=new_trace_id)
    route: AICallRoute | None = None
    model_name: str | None = None
    provider_key: str | None = None
    provider_row_id: str | None = None
    is_local: bool = False
    cost_per_1k_input_micros: int | None = None
    cost_per_1k_output_micros: int | None = None

    @property
    def resolved_route(self) -> AICallRoute:
        if self.route is not None:
            return self.route
        task = get_task_def(self.task_id)
        return AICallRoute(task.route.value) if task is not None else AICallRoute.CHAT


def _pricing_from_model(model: Any) -> tuple[int | None, int | None]:
    """Read declared pricing from a model row's ``settings`` JSONB (if any)."""
    settings = getattr(model, "settings", None)
    if not isinstance(settings, dict):
        return None, None

    def _as_int(key: str) -> int | None:
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    return _as_int("cost_per_1k_input_micros"), _as_int("cost_per_1k_output_micros")


def _is_local_url(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")


def context_for_task(
    task_id: str,
    *,
    provider: Any = None,
    model: Any = None,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    patient_id: UUID | None = None,
    trace_id: str | None = None,
) -> CallAuditContext:
    """Build an audit context from the resolved provider/model rows."""
    cost_in, cost_out = _pricing_from_model(model)
    model_local = getattr(model, "is_local", None)
    provider_local = getattr(provider, "is_local", None)
    is_local = (
        bool(model_local) if model_local is not None else bool(provider_local)
    ) or _is_local_url(getattr(provider, "api_base", None))
    return CallAuditContext(
        task_id=str(task_id),
        tenant_id=tenant_id,
        user_id=user_id,
        patient_id=patient_id,
        provider_id=getattr(provider, "id", None),
        model_id=getattr(model, "id", None),
        trace_id=trace_id or new_trace_id(),
        model_name=getattr(model, "model_name", None),
        provider_key=getattr(provider, "preset_key", None),
        provider_row_id=str(provider.id) if getattr(provider, "id", None) else None,
        is_local=is_local,
        cost_per_1k_input_micros=cost_in,
        cost_per_1k_output_micros=cost_out,
    )


def estimate_cost_micros(
    ctx: CallAuditContext, prompt_tokens: int | None, completion_tokens: int | None
) -> int:
    """Integer micro-units from the pre-contract model row's declared per-1k prices."""
    total = 0
    if prompt_tokens and ctx.cost_per_1k_input_micros:
        total += round(prompt_tokens * ctx.cost_per_1k_input_micros / 1000)
    if completion_tokens and ctx.cost_per_1k_output_micros:
        total += round(completion_tokens * ctx.cost_per_1k_output_micros / 1000)
    return total


def _has_pre_contract_price(ctx: CallAuditContext) -> bool:
    """Whether a complete pre-contract per-1k price pair was declared.

    A half-declared pair cannot produce an honest total: pricing the declared
    side and silently treating the other as free would fabricate a number.
    """
    return ctx.cost_per_1k_input_micros is not None and ctx.cost_per_1k_output_micros is not None


def _override_rows(overrides: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "providerRef": str(row.provider_ref) if row.provider_ref else None,
            "provider": row.provider_key,
            "model": row.model,
            "match": row.match,
            "inputMicrosPerMillion": int(row.input_micros_per_million),
            "outputMicrosPerMillion": int(row.output_micros_per_million),
            "metering": row.metering,
            "id": str(row.id),
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in overrides
    ]


def resolve_call_price(
    ctx: CallAuditContext,
    overrides: list[Mapping[str, Any]],
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> tuple[int | None, str, str | None]:
    """Return ``(cost_micros, price_source, price_version)`` for one call."""
    from app.ai.model_pricing import resolve_price
    from app.ai.model_pricing_data import MODEL_PRICING_COMPILED, MODEL_PRICING_ROWS

    resolution = resolve_price(
        rows=MODEL_PRICING_ROWS,
        compiled=MODEL_PRICING_COMPILED,
        model=ctx.model_name or "",
        provider_key=ctx.provider_key,
        provider_row_id=ctx.provider_row_id,
        is_local=ctx.is_local,
        user_rows=overrides,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
    )
    if resolution is not None:
        return resolution.cost_micros, resolution.source, resolution.price_version
    if _has_pre_contract_price(ctx):
        if prompt_tokens is None or completion_tokens is None:
            return None, "legacy", "pre-contract"  # gate-allow: legacy
        return (
            estimate_cost_micros(ctx, prompt_tokens, completion_tokens),
            "legacy",  # gate-allow: legacy
            "pre-contract",
        )
    return None, "unknown", None


def classify_error(error: BaseException) -> tuple[AICallStatus, str]:
    """Map a raised error onto the closed status vocabulary + a machine class."""
    if isinstance(error, asyncio.CancelledError):
        return AICallStatus.CANCELLED, type(error).__name__
    name = type(error).__name__.lower()
    message = str(error).lower()
    if any(hint in name for hint in _CANCEL_HINTS) or any(
        hint in message for hint in _CANCEL_HINTS
    ):
        return AICallStatus.CANCELLED, type(error).__name__
    if (
        isinstance(error, (TimeoutError, asyncio.TimeoutError))
        or any(hint in name for hint in _TIMEOUT_HINTS)
        or any(hint in message for hint in _TIMEOUT_HINTS)
    ):
        return AICallStatus.TIMEOUT, type(error).__name__
    return AICallStatus.ERROR, type(error).__name__


async def record_call(
    ctx: CallAuditContext,
    *,
    started_at: datetime,
    latency_ms: int,
    status: AICallStatus,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error_class: str | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Write one audit row. Returns ``False`` when the row was dropped.

    Fail-soft by contract: any failure (no database, unknown task, FK race) is
    logged and swallowed — the model call it describes is never affected.
    """
    try:
        from app.core.database import AsyncSessionLocal

        if AsyncSessionLocal is None:
            return False
        payload = {
            "model_name": ctx.model_name,
            **(meta or {}),
        }
        async with AsyncSessionLocal() as db:
            task_type_id = await ensure_task_def(db, ctx.task_id)
            if task_type_id is None:
                logger.warning("AI call audit skipped: unknown task '%s'", ctx.task_id)
                await db.rollback()
                return False
            from sqlalchemy import select

            from app.models.ai_task_model import AICallLog, AIPriceOverride

            overrides = []
            if ctx.tenant_id is not None:
                rows = await db.execute(
                    select(AIPriceOverride).where(AIPriceOverride.tenant_id == ctx.tenant_id)
                )
                overrides = _override_rows(list(rows.scalars()))
            usage_source = (
                "provider"
                if prompt_tokens is not None and completion_tokens is not None
                else "partial"
                if prompt_tokens is not None or completion_tokens is not None
                else "unknown"
            )
            cost_micros, price_source, price_version = resolve_call_price(
                ctx, overrides, prompt_tokens, completion_tokens
            )

            db.add(
                AICallLog(
                    task_type_id=task_type_id,
                    route=ctx.resolved_route,
                    provider_id=ctx.provider_id,
                    model_id=ctx.model_id,
                    patient_id=ctx.patient_id,
                    started_at=started_at,
                    latency_ms=max(int(latency_ms), 0),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    provider_key=ctx.provider_key,
                    cost_micros=cost_micros,
                    usage_source=usage_source,
                    estimate_method=None,
                    price_source=price_source,
                    price_version=price_version,
                    status=status,
                    error_class=(error_class or None),
                    trace_id=ctx.trace_id,
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    meta=payload,
                    meta_schema_version=META_SCHEMA_VERSION,
                )
            )
            await db.commit()
        return True
    except Exception as exc:
        logger.warning("AI call audit write failed (%s): %s", ctx.task_id, exc)
        return False


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    """Extract (prompt, completion) tokens from a LangChain ``LLMResult``."""
    prompt = completion = None
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
    if prompt is None or completion is None:
        generations = getattr(response, "generations", None) or []
        for batch in generations:
            for generation in batch:
                message = getattr(generation, "message", None)
                usage_metadata = getattr(message, "usage_metadata", None)
                if isinstance(usage_metadata, dict):
                    prompt = usage_metadata.get("input_tokens", prompt)
                    completion = usage_metadata.get("output_tokens", completion)
    return (
        int(prompt) if isinstance(prompt, int) else None,
        int(completion) if isinstance(completion, int) else None,
    )


class AICallAuditHandler(AsyncCallbackHandler):
    """LangChain callback handler that writes one row per model call.

    One handler instance can serve many calls (an agent reuses its model across
    turns), so run state is keyed by LangChain's ``run_id``.
    """

    def __init__(self, ctx: CallAuditContext) -> None:
        self.ctx = ctx
        self._runs: dict[str, tuple[datetime, float]] = {}
        self._streamed: set[str] = set()

    def _start(self, run_id: Any) -> None:
        self._runs[str(run_id)] = (datetime.now(UTC), time.monotonic())

    def _pop(self, run_id: Any) -> tuple[datetime, int, bool] | None:
        key = str(run_id)
        started = self._runs.pop(key, None)
        streamed = key in self._streamed
        self._streamed.discard(key)
        if started is None:
            return None
        return started[0], int((time.monotonic() - started[1]) * 1000), streamed

    async def on_chat_model_start(self, *args: Any, run_id: Any = None, **kwargs: Any) -> None:
        self._start(run_id)

    async def on_llm_start(self, *args: Any, run_id: Any = None, **kwargs: Any) -> None:
        self._start(run_id)

    async def on_llm_new_token(self, *args: Any, run_id: Any = None, **kwargs: Any) -> None:
        if run_id is not None:
            self._streamed.add(str(run_id))

    async def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        timing = self._pop(run_id)
        if timing is None:
            return
        started_at, latency_ms, streamed = timing
        prompt_tokens, completion_tokens = _usage_tokens(response)
        await record_call(
            self.ctx,
            started_at=started_at,
            latency_ms=latency_ms,
            status=AICallStatus.OK,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            meta={"streamed": streamed},
        )

    async def on_llm_error(
        self, error: BaseException, *, run_id: Any = None, **kwargs: Any
    ) -> None:
        timing = self._pop(run_id)
        if timing is None:
            return
        started_at, latency_ms, _streamed = timing
        status, error_class = classify_error(error)
        await record_call(
            self.ctx,
            started_at=started_at,
            latency_ms=latency_ms,
            status=status,
            error_class=error_class[:80],
        )


def attach_call_audit(model: Any, ctx: CallAuditContext) -> Any:
    """Attach the audit handler to a chat model and return it unchanged.

    Instance-level callbacks are inherited by tool-bound runnables and by every
    ``invoke``/``stream`` on the model, which is why this is the one seam.
    """
    if model is None or not ctx.task_id:
        return model
    handler = AICallAuditHandler(ctx)
    existing = getattr(model, "callbacks", None)
    if existing is None:
        model.callbacks = [handler]
    elif isinstance(existing, list):
        model.callbacks = [*existing, handler]
    else:
        existing.add_handler(handler)
    return model

"""Decision probe (D13/D18): one minimal typed question under the abort
timeout; a classified failure persists nothing. Also the no-oracle contract
(S10): without a valid key the probe maps to the auth error kinds.
"""

import time
from dataclasses import dataclass
from typing import Any

from app.models.ai_provider_model import AIModel, AIProviderModel

from ..types import DECISION_PROBE_TIMEOUT_SECONDS, DecisionCapability
from .client import (
    SystemOneCallParams,
    SystemOneClient,
    TypeSafeClientConfig,
    TypeSafeError,
    TypeSafeQuestion,
    TypeSafeSdkSystemOneClient,
    resolve_systemone_base_url,
)

PROBE_QUESTION_ID = "__probe__"


@dataclass(frozen=True)
class DecisionProbeResult:
    ok: bool
    latency_ms: int
    resolved_model: str | None = None
    status: int | None = None
    kind: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DecisionProbeInput:
    provider: AIProviderModel
    model: AIModel
    api_key: str
    timeout_seconds: float | None = None
    transport: Any = None
    client: SystemOneClient | None = None


def probe_decision_provider(probe_input: DecisionProbeInput) -> DecisionProbeResult:
    started_at = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    base_url = resolve_systemone_base_url(probe_input.provider, probe_input.model)
    if base_url is None:
        return DecisionProbeResult(
            ok=False, latency_ms=0, error="The provider base URL is missing or invalid."
        )
    timeout_seconds = probe_input.timeout_seconds or DECISION_PROBE_TIMEOUT_SECONDS
    client: SystemOneClient = probe_input.client or TypeSafeSdkSystemOneClient(
        TypeSafeClientConfig(
            api_key=probe_input.api_key,
            base_url=base_url,
            model=probe_input.model.model_name,
            timeout_seconds=timeout_seconds,
            max_retries=0,
            transport=probe_input.transport,
        )
    )
    try:
        result = client.system_one(
            SystemOneCallParams(
                state="connectivity check",
                questions={
                    PROBE_QUESTION_ID: TypeSafeQuestion(
                        type=DecisionCapability.BOOLEAN_GATE,
                        instructions="Is this a working decision endpoint? Answer true.",
                    )
                },
            )
        )
        return DecisionProbeResult(
            ok=True,
            latency_ms=elapsed_ms(),
            resolved_model=result.model or None,
        )
    except TypeSafeError as error:
        return DecisionProbeResult(
            ok=False,
            latency_ms=elapsed_ms(),
            status=error.status,
            kind=error.kind.value,
            error=error.message[:300],
        )
    except Exception as error:
        return DecisionProbeResult(
            ok=False,
            latency_ms=elapsed_ms(),
            error=str(error)[:300],
        )

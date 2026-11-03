"""TypeSafe System One transport client (``typesafe-sdk``).

Ported from study's Phase 3 Python reference; the SDK import is confined to
the AI layer (alignment gate SDK_RE — ``typesafe-sdk`` inside ``app/ai``).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from typesafe_sdk import (
    Choice,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeClient,
)
from typesafe_sdk import TypeSafeAPITimeoutError as _SdkTimeoutError

from app.ai.providers.enums import SystemOneErrorKind
from app.models.ai_provider_model import AIModel, AIProviderModel

from ..types import DecisionAnswer, DecisionCapability

DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RETRIES = 2


class TypeSafeError(Exception):
    def __init__(
        self,
        kind: SystemOneErrorKind,
        message: str,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status


@dataclass(frozen=True)
class TypeSafeUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class TypeSafeResult:
    model: str
    answers: dict[str, DecisionAnswer]
    usage: TypeSafeUsage | None = None


@dataclass(frozen=True)
class TypeSafeQuestion:
    type: DecisionCapability
    instructions: str
    criteria: Mapping[str, str | None] | None = None
    levels: tuple[str, ...] | None = None
    true_criteria: str | None = None
    false_criteria: str | None = None

    def to_payload(self) -> Noul | Choice | Score:
        if self.type == DecisionCapability.CHOICE:
            return Choice(
                instructions=self.instructions,
                criteria=dict(self.criteria or {}),
            )
        if self.type == DecisionCapability.SCORE:
            return Score(
                instructions=self.instructions,
                criteria=list(self.levels or ()),
            )
        if self.true_criteria is None and self.false_criteria is None:
            return Noul(instructions=self.instructions)
        return Noul(
            instructions=self.instructions,
            criteria={"true": self.true_criteria, "false": self.false_criteria},
        )


@dataclass(frozen=True)
class SystemOneCallParams:
    state: str
    questions: Mapping[str, TypeSafeQuestion]


class SystemOneClient(Protocol):
    def system_one(self, params: SystemOneCallParams) -> TypeSafeResult: ...


@dataclass(frozen=True)
class TypeSafeClientConfig:
    api_key: str
    base_url: str
    model: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    transport: Any = None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _answer_of(raw: Any) -> DecisionAnswer | None:
    answer_type = getattr(raw, "type", None)
    if answer_type == "choice":
        probabilities = dict(getattr(raw, "probabilities", {}) or {})
        return DecisionAnswer(
            type=DecisionCapability.CHOICE,
            choice=getattr(raw, "choice", None),
            probabilities={str(key): float(value) for key, value in probabilities.items()},
            confidence=_optional_float(getattr(raw, "confidence", None)),
        )
    if answer_type == "score":
        legend_raw = dict(getattr(raw, "legend", {}) or {})
        legend = {
            str(key): value if isinstance(value, str) else str(value)
            for key, value in legend_raw.items()
        }
        probabilities = dict(getattr(raw, "probabilities", {}) or {})
        return DecisionAnswer(
            type=DecisionCapability.SCORE,
            score=_optional_float(getattr(raw, "score", None)),
            legend=legend,
            probabilities={str(key): float(value) for key, value in probabilities.items()},
            confidence=_optional_float(getattr(raw, "confidence", None)),
        )
    if answer_type == "noul":
        return DecisionAnswer(
            type=DecisionCapability.BOOLEAN_GATE,
            noul=_optional_float(getattr(raw, "noul", None)),
        )
    return None


def _to_result(result: Any) -> TypeSafeResult:
    raw_answers = dict(getattr(result, "answers", {}) or {})
    answers: dict[str, DecisionAnswer] = {}
    for question_id, raw in raw_answers.items():
        answer = _answer_of(raw)
        if answer is not None:
            answers[str(question_id)] = answer
    usage_raw = getattr(result, "usage", None)
    usage: TypeSafeUsage | None = None
    if usage_raw is not None:
        usage = TypeSafeUsage(
            input_tokens=getattr(usage_raw, "input_tokens", None),
            output_tokens=getattr(usage_raw, "output_tokens", None),
        )
    return TypeSafeResult(
        model=str(getattr(result, "model", "") or ""),
        answers=answers,
        usage=usage,
    )


_AUTH_STATUSES = {401, 402, 403}
_VALIDATION_STATUSES = {400, 422}
_OVERLOADED_STATUSES = {503, 529}


def map_transport_error(error: Exception) -> TypeSafeError:
    if isinstance(error, TypeSafeError):
        return error
    if isinstance(error, TypeSafeAPIError):
        status = getattr(error, "status", None)
        if status in _AUTH_STATUSES:
            return TypeSafeError(SystemOneErrorKind.AUTH, str(error), status)
        if status in _VALIDATION_STATUSES:
            return TypeSafeError(SystemOneErrorKind.VALIDATION, str(error), status)
        if status == 429:
            return TypeSafeError(SystemOneErrorKind.RATE_LIMIT, str(error), status)
        if status in _OVERLOADED_STATUSES:
            return TypeSafeError(SystemOneErrorKind.OVERLOADED, str(error), status)
        return TypeSafeError(SystemOneErrorKind.UNAVAILABLE, str(error), status)
    if isinstance(error, _SdkTimeoutError):
        return TypeSafeError(SystemOneErrorKind.TIMEOUT, str(error))
    if isinstance(error, TypeSafeAPIConnectionError):
        return TypeSafeError(SystemOneErrorKind.UNAVAILABLE, str(error))
    return TypeSafeError(SystemOneErrorKind.UNAVAILABLE, str(error)[:300])


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return not parsed.username and not parsed.password


def strip_chat_version_segment(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed[: -len("/v1")]
    return trimmed


KEYLESS_SYSTEMONE_API_KEY = "keyless-local"
"""The official SDK requires a non-empty API key (D9 — no hand-rolled
wire clients), so keyless presets resolve with this sentinel instead of
failing needs-key: a keyless server (local Ollama) ignores the
Authorization header. Never a real secret; keyed presets keep the
needs-key failure."""


def preset_is_keyless(preset_key: str | None) -> bool:
    """The preset expects no key (no ``key_url`` in the canonical data)."""
    if not preset_key:
        return False
    from app.ai.providers.presets_data import PRESETS

    row = PRESETS.get(preset_key)
    return row is not None and row["key_url"] is None


def resolve_systemone_base_url(provider: AIProviderModel, model: AIModel) -> str | None:
    if provider.preset_key:
        from app.ai.providers.presets import decision_models_for_preset

        for entry in decision_models_for_preset(provider.preset_key):
            if entry["id"] == model.model_name:
                override = entry.get("base_url_override")
                if isinstance(override, str) and override and _is_http_url(override):
                    return override.rstrip("/")
                break
    raw = (provider.api_base or "").strip()
    if not _is_http_url(raw):
        return None
    return strip_chat_version_segment(raw)


class TypeSafeSdkSystemOneClient:
    def __init__(self, config: TypeSafeClientConfig) -> None:
        retry = RetryPolicy(
            max_retries=config.max_retries
            if config.max_retries is not None
            else DEFAULT_MAX_RETRIES,
            timeout=config.timeout_seconds
            if config.timeout_seconds is not None
            else DEFAULT_TIMEOUT_SECONDS,
        )
        self._client = TypeSafeClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            retry=retry,
            timeout=config.timeout_seconds
            if config.timeout_seconds is not None
            else DEFAULT_TIMEOUT_SECONDS,
            **({"transport": config.transport} if config.transport is not None else {}),
        )

    def system_one(self, params: SystemOneCallParams) -> TypeSafeResult:
        payload = {
            question_id: question.to_payload() for question_id, question in params.questions.items()
        }
        try:
            result = self._client.system_one(state=params.state, questions=payload)
        except Exception as error:
            raise map_transport_error(error) from error
        return _to_result(result)

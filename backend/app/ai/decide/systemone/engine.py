from dataclasses import dataclass
from typing import Any

from app.ai.providers.enums import DecisionTransport

from ..types import DecisionOutcome, DecisionRequest
from .client import (
    SystemOneCallParams,
    SystemOneClient,
    TypeSafeClientConfig,
    TypeSafeSdkSystemOneClient,
)
from .projection import (
    build_question_set,
    build_tool_dispatch_questions,
    question_outcome,
    tool_dispatch_outcome,
)


@dataclass(frozen=True)
class SystemOneEngineParams:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float | None = None
    max_retries: int | None = None
    transport: Any = None
    client: SystemOneClient | None = None


class SystemOneDecisionEngine:
    transport = DecisionTransport.SYSTEMONE

    def __init__(self, params: SystemOneEngineParams) -> None:
        self._model = params.model
        if params.client is not None:
            self._client = params.client
        else:
            self._client = TypeSafeSdkSystemOneClient(
                TypeSafeClientConfig(
                    api_key=params.api_key,
                    base_url=params.base_url,
                    model=params.model,
                    timeout_seconds=params.timeout_seconds,
                    max_retries=params.max_retries,
                    transport=params.transport,
                )
            )

    def decide(self, request: DecisionRequest) -> DecisionOutcome:
        questions = list(request.questions or ())
        if questions:
            result = self._client.system_one(
                SystemOneCallParams(
                    state=request.input,
                    questions=build_question_set(questions),
                )
            )
            return question_outcome(result, questions, self._model)
        tools = list(request.tools or ())
        result = self._client.system_one(
            SystemOneCallParams(
                state=request.input,
                questions=build_tool_dispatch_questions(
                    tools, dict(request.catalog_entities or {})
                ),
            )
        )
        return tool_dispatch_outcome(result, tools, self._model)

"""LLM builders for each LLM-capable ``ProviderType`` (the model factory).

This is the ONLY application module that may import LangChain chat classes
or provider SDK packages (ADR-0008): every ``BaseChatModel`` used anywhere in
the app is built here and injected by ``AIProviderService.get_llm``.

A builder is a plain function that accepts keyword-only LLM configuration
(``api_key``, ``base_url``, ``model_name``, ``temperature``, ``max_tokens``)
and returns a LangChain ``BaseChatModel``. Builders are intentionally
side-effect-free and stateless — all tenancy/runtime context lives in the
caller.

Only OpenAI is wired today (the project is OpenAI-compatible by default).
Anthropic / Ollama / Azure OpenAI / Bedrock are registered as stubs that raise
``NotImplementedError`` so the provider enum + registry can be exercised end
to end before the SDK wiring lands. To finish a provider:

1. Add the SDK to ``backend/requirements.txt`` (e.g. ``langchain-anthropic``).
2. Implement its builder here.
3. (No registry change needed — it already points here.)

The ``mock`` provider (:class:`MockMedicalChatModel`) is the deliberate
exception to "no local BaseChatModel implementations": a dependency-free
scripted model used for deterministic demos/screenshots/offline dev without
any API key. It still runs the real graph + real DB tools — only the LLM
reasoning is scripted.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Iterator, List, Optional, Tuple

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def build_openai(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    """Build a ``ChatOpenAI`` (OpenAI-compatible: OpenAI, LocalAI, vLLM, ...).

    ``reasoning_effort`` (model-row setting, e.g. ``"none"``) is forwarded
    verbatim — reasoning-family models require it for function-tool calls
    on chat completions. ``None`` keeps the provider default.
    """
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )


def build_anthropic(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    raise NotImplementedError(
        "Anthropic provider is reserved but not wired. Add langchain-anthropic to "
        "requirements.txt and implement this builder in app.ai.chat_models."
    )


def build_ollama(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    raise NotImplementedError(
        "Ollama provider is reserved but not wired. Add langchain-ollama (or "
        "langchain-community) to requirements.txt and implement this builder."
    )


def build_azure_openai(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    raise NotImplementedError("Azure OpenAI provider is reserved but not wired.")


def build_bedrock(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    raise NotImplementedError("Bedrock provider is reserved but not wired.")


# ---------------------------------------------------------------------------
# Mock provider — deterministic scripted model for demos/screenshots/dev.
# ---------------------------------------------------------------------------

#: Tool-call ids used by the mock's scripted plan (stable, referenced by the
#: ToolMessage routing below).
MOCK_EXAMS_CALL_ID = "call_mock_exams"
MOCK_DETAILS_CALL_ID = "call_mock_details"


def _mock_final_answer(history: List[BaseMessage]) -> str:
    """Build the final markdown answer from the mock's tool results.

    Parses the ``get_examination_details`` ToolMessage and formats its
    examination + associated biomarker observations with the family citation
    syntax (``[Ref: type=uuid]``) so the frontend renders the same
    EXAMINATION / OBSERVATION citation chips a real LLM answer would.
    """
    details: dict = {}
    for msg in history:
        if isinstance(msg, ToolMessage) and msg.tool_call_id == MOCK_DETAILS_CALL_ID:
            try:
                details = json.loads(msg.content)
            except (TypeError, ValueError):
                details = {}
            break

    exam_date = details.get("date") or "an unknown date"
    notes = (details.get("notes") or "No notes were recorded.").split(". ")[0].rstrip(".") + "."

    # The examination's associated observations ARE the "results of this
    # exam". Rows without a unit are non-quantity entries (e.g. STATE
    # biomarkers) — keep the table to lab-style quantity results.
    lines = [
        f"Your latest examination was on **{exam_date}**. The note says: {notes} "
        f"[Ref: examination={details.get('id')}]",
        "",
        "**Results**",
        "",
        "| Test | Result | Unit | Reference |",
        "| --- | --- | --- | --- |",
    ]
    for obs in details.get("biomarkers") or []:
        if not obs.get("unit"):
            continue
        lines.append(
            f"| {obs['name']} | {obs.get('value')} | {obs['unit']} | [Ref: observation={obs['id']}] |"
        )
    lines += [
        "",
        "If you want, I can also summarize whether these values are in a typical healthy range.",
    ]
    return "\n".join(lines)


class MockMedicalChatModel(BaseChatModel):
    """Scripted, deterministic chat model for the ``mock`` provider type.

    Three-turn plan over the real agentic graph (real DB tools, no network):

    1. ``get_recent_examinations`` → find the latest examination
    2. ``get_examination_details`` → its notes + associated observations
    3. final markdown answer templated from those results, using the citation
       syntax the chat UI renders as EXAMINATION / OBSERVATION chips

    Used by ``seed_demo.py`` (mock provider + chat assignment) so demo
    instances and UI captures produce a full, realistic assistant answer
    without any API key. Never used unless explicitly configured.
    """

    model_name: str = "mock-medical"

    @property
    def _llm_type(self) -> str:
        return "mock-medical"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockMedicalChatModel":
        # The script already "knows" which tools to call; binding is a no-op.
        return self

    # -- planning ----------------------------------------------------------

    @staticmethod
    def _plan(messages: List[BaseMessage]) -> Tuple[str, Optional[List[dict]]]:
        """Scripted plan over three turns:

        1. no tool results yet          → ``get_recent_examinations``
        2. examinations result present  → ``get_examination_details(<id>)``
        3. details result present       → final markdown answer
        """
        have_details = any(
            isinstance(m, ToolMessage) and m.tool_call_id == MOCK_DETAILS_CALL_ID
            for m in messages
        )
        if have_details:
            return "answer", None

        have_exams = any(
            isinstance(m, ToolMessage) and m.tool_call_id == MOCK_EXAMS_CALL_ID
            for m in messages
        )
        if have_exams:
            exam_id = None
            for m in messages:
                if isinstance(m, ToolMessage) and m.tool_call_id == MOCK_EXAMS_CALL_ID:
                    try:
                        exams = json.loads(m.content)
                        exam_id = (exams[0] or {}).get("id") if exams else None
                    except (TypeError, ValueError):
                        exam_id = None
                    break
            return "tools", [
                {
                    "id": MOCK_DETAILS_CALL_ID,
                    "name": "get_examination_details",
                    "args": {"examination_id": exam_id or ""},
                }
            ]

        return "tools", [
            {"id": MOCK_EXAMS_CALL_ID, "name": "get_recent_examinations", "args": {"limit": 5}}
        ]

    # -- sync generation ----------------------------------------------------

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        mode, calls = self._plan(messages)
        if mode == "tools":
            message = AIMessage(content="", tool_calls=calls)
        else:
            message = AIMessage(content=_mock_final_answer(messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    # -- streaming -----------------------------------------------------------

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from _mock_stream_sync(messages)

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in _mock_stream_sync(messages):
            yield chunk


def _mock_stream_sync(messages: List[BaseMessage]) -> Iterator[ChatGenerationChunk]:
    """Chunked stream for the mock's two-turn plan.

    Tool-call turn: one chunk carrying the full ``tool_call_chunks`` (the
    graph merges them by index into ``tool_calls``). Answer turn: content
    split into small word-group chunks so SSE consumers see realistic
    incremental deltas.
    """
    mode, calls = MockMedicalChatModel._plan(messages)
    if mode == "tools":
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": json.dumps(call["args"]),
                        "id": call["id"],
                        "index": i,
                        "type": "tool_call_chunk",
                    }
                    for i, call in enumerate(calls)
                ],
            )
        )
        return

    text = _mock_final_answer(messages)
    words = text.split(" ")
    step = 3
    for i in range(0, len(words), step):
        piece = " ".join(words[i : i + step])
        if i + step < len(words):
            piece += " "
        yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


def build_mock(
    *,
    api_key: Optional[str],
    base_url: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> BaseChatModel:
    """Build the deterministic mock chat model (provider_type ``"mock"``).

    All LLM-config kwargs are accepted and ignored so the provider registry
    can treat every builder uniformly.
    """
    return MockMedicalChatModel(model_name=model_name or "mock-medical")

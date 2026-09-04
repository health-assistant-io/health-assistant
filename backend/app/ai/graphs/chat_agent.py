"""LangGraph mirror of the agentic-chat reasoning loop (Phases 3.2 + 3.3).

``run_chat_graph`` reproduces :func:`app.ai.agents.chat_agent.run_reasoning_loop`
event-for-event as a StateGraph, selected by ``AI_AGENT_ENGINE=loop|graph``.
Both engines yield the same ``(kind, data)`` tuples, so
:func:`app.ai.agents.chat_agent.stream_loop_as_sse` and the non-streaming
consumer work unchanged — the SSE sentinel contract is frozen.

Decision point (create_agent vs custom): custom StateGraph. The loop
intercepts HITL proposals at tool-result level (trimmed feedback substituted
into the ToolMessage, no citation, proactive message persistence) and has
conditional final-save semantics (streaming always, non-streaming only on a
clean break) — none of that is hostable in ``create_agent`` middleware
without behavior risk. Ratified by the integrator (Phase 3.3).

Graph shape::

    START -> agent_step -> route_step ──(no tool calls)──────────> finalize
                    │               └─(tool calls)-> tool_exec -> route_exec
                    └──────────────(under cap)<─────────────────────┘
                                       (ask_user)-> await_user -> route_wait
                                                    (others cap)-> finalize

Phase 3.3 HITL ruling: ``ask_user`` PAUSES the turn at the question card —
``tool_exec`` emits the card + persists proactively (committed by the
superstep checkpoint), then hands to the side-effect-free ``await_user`` node
whose ``interrupt()`` is its first statement, so NOTHING re-executes on
resume. ``/resume`` delivers the resolution summary as ``Command(resume=...)``
— it becomes the ask_user ``ToolMessage`` and the model continues the same
conversation. ``propose_*`` keeps continue-after-propose (card events +
HitlTask rows + idempotent /resolve, unchanged).

Fault tolerance (ratified): graph-default retry covers ``agent_step`` (LLM
calls are read-only; state commits only on node success). ``tool_exec`` is
pinned to a single attempt — clinical tools stay non-idempotent even
checkpointed. There is intentionally NO error_handler: exceptions bubble to
the SSE error classifier exactly like the loop engine (an error->finalize
handler would persist partial content and change the failure contract).

Checkpoint-serializability: the state carries ONLY serializable data
(messages, counters, task dicts). Per-run handles (LLM runnable, tools,
chat service, user/tenant context) ride in ``config["configurable"]`` under
the ``runtime`` key — never checkpointed — and must be supplied again by the
resume caller (:func:`resume_interrupted_chat_graph`).
"""

from __future__ import annotations

import logging
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Tuple,
    TypedDict,
)
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt

from app.ai.agents.chat_agent import run_reasoning_loop
from app.ai.agents.hitl import (
    _hitl_llm_feedback,
    _hitl_proposal_note,
    _parse_hitl_proposal,
)
from app.ai.graphs.checkpointer import get_runtime_saver
from app.core.config import settings

logger = logging.getLogger(__name__)


def _stream_writer():
    """Stream-writer accessor that also works under direct node invocation
    (``graph.nodes[...]`` tests run outside the Pregel loop, where
    ``get_stream_writer`` raises — events are simply dropped there)."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


class ChatGraphState(TypedDict):
    # Serializable per-run configuration (checkpointed: streaming/caps must
    # survive an interrupt resume).
    streaming: bool
    session_id: Optional[str]
    max_iterations: int
    # Mutable run state (all serializable).
    history: List[Any]
    iteration: int  # LLM calls made so far
    total_content: str
    all_tool_calls: List[Dict[str, Any]]
    all_citations: List[str]
    all_tasks: List[Dict[str, Any]]
    # agent_step -> tool_exec handoff: None = no tool calls this iteration
    # (clean break); [] = tool calls consumed by tool_exec.
    pending_tool_calls: Optional[List[Dict[str, Any]]]
    # tool_exec -> await_user handoff (ask_user only): the interrupt payload
    # (the HITL task dict) + the tool_call_id awaiting its ToolMessage.
    pending_interrupt: Optional[Dict[str, Any]]


def _chat_graph_nodes():
    async def agent_step(state: ChatGraphState, config) -> Dict[str, Any]:
        """One LLM call (streamed or not) — provider-quirk content dedup is
        copied verbatim from the loop engine for event parity."""
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        llm_with_tools = runtime["llm_with_tools"]
        history = list(state["history"])
        streaming = state["streaming"]
        total_content = state["total_content"]

        tool_calls = None
        if streaming:
            final_chunk = None
            tool_name_yielded: set = set()
            content_received = ""
            content_yielded_iter = ""

            async for chunk in llm_with_tools.astream(history):
                if chunk.tool_call_chunks:
                    for tc_chunk in chunk.tool_call_chunks:
                        tc_name = tc_chunk.get("name")
                        if tc_name and tc_name not in tool_name_yielded:
                            emit("tool_call_start", tc_name)
                            tool_name_yielded.add(tc_name)

                if chunk.content:
                    # Some providers re-emit accumulated content rather than
                    # true deltas — reconcile against what we've received.
                    if (
                        content_received
                        and chunk.content.startswith(content_received)
                        and len(chunk.content) > len(content_received)
                    ):
                        content_received = chunk.content
                    else:
                        content_received += chunk.content

                    if not total_content:
                        delta = content_received
                    elif content_received.startswith(total_content):
                        delta = content_received[len(total_content) :]
                    elif total_content.startswith(content_received):
                        delta = ""
                    else:
                        delta = content_received

                    if content_yielded_iter and delta.startswith(content_yielded_iter):
                        actual_yield = delta[len(content_yielded_iter) :]
                    else:
                        actual_yield = delta

                    if actual_yield:
                        emit("content", actual_yield)
                        content_yielded_iter += actual_yield
                        total_content += actual_yield

                final_chunk = chunk if final_chunk is None else final_chunk + chunk

            tool_calls = final_chunk.tool_calls if final_chunk else None
            content_for_history = content_received
        else:
            response = await llm_with_tools.ainvoke(history)
            if response.content:
                if not total_content:
                    delta = response.content
                elif response.content.startswith(total_content):
                    delta = response.content[len(total_content) :]
                elif total_content.startswith(response.content):
                    delta = ""
                else:
                    delta = response.content
                if delta:
                    emit("content", delta)
                    total_content += delta
            tool_calls = response.tool_calls
            content_for_history = response.content

        updates: Dict[str, Any] = {
            "iteration": state["iteration"] + 1,
            "total_content": total_content,
            "pending_tool_calls": list(tool_calls) if tool_calls else None,
            "content_for_history": content_for_history,
        }
        if tool_calls:
            history.append(
                AIMessage(content=content_for_history, tool_calls=tool_calls)
            )
            updates["history"] = history
        return updates

    async def tool_exec(state: ChatGraphState, config) -> Dict[str, Any]:
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        history = list(state["history"])
        tools = runtime["tools"]
        streaming = state["streaming"]
        session_id = state["session_id"]
        chat_session_service = runtime["chat_session_service"]
        log_label = runtime["log_label"]
        total_content = state["total_content"]
        all_tool_calls = list(state["all_tool_calls"])
        all_citations = list(state["all_citations"])
        all_tasks = list(state["all_tasks"])
        proactive_message = runtime["proactive_message"]
        pending_interrupt = state["pending_interrupt"]

        logger.info(
            f"{log_label}: tool calls detected, entering reasoning loop "
            f"iteration {state['iteration']}"
        )

        async def save_proactive() -> None:
            """Persist the assistant message the moment a HITL card is emitted
            so the task survives stream interruptions (breaking /resolve (404)
            and /resume otherwise). Streaming only; once per run."""
            nonlocal proactive_message
            if not (streaming and session_id and proactive_message is None):
                return
            try:
                proactive_message = await chat_session_service.save_message(
                    session_id=session_id,
                    role="assistant",
                    content={"text": total_content},
                    tool_calls=list(all_tool_calls),
                    citations=list(all_citations),
                    tasks=list(all_tasks),
                )
                logger.info(
                    f"HITL task proactively saved to message "
                    f"{proactive_message.id} (task_type="
                    f"{all_tasks[-1].get('task_type') if all_tasks else '?'})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to proactively save HITL task: {e}", exc_info=True
                )

        for tool_call in state["pending_tool_calls"] or []:
            tool_name = tool_call["name"]
            selected_tool = next((t for t in tools if t.name == tool_name), None)
            if selected_tool:
                emit("tool_call_exec", tool_name)
                observation = await selected_tool.ainvoke(tool_call["args"])

                hitl_task = _parse_hitl_proposal(observation)
                if hitl_task and (
                    streaming
                    and hitl_task.get("task_type") == "ask_user"
                    and pending_interrupt is None
                ):
                    # ask_user (Phase 3.3 ruling): the turn PAUSES at the
                    # question card. Emit the card + persist proactively here
                    # (committed by the superstep checkpoint), then hand off
                    # to the side-effect-free await_user node whose interrupt()
                    # is its first statement — nothing re-executes on resume.
                    # A second ask_user in the same iteration (the tool prompt
                    # forbids it) degrades to continue-mode below.
                    all_tasks.append(hitl_task)
                    note = _hitl_proposal_note(observation)
                    feedback = _hitl_llm_feedback(hitl_task, note)
                    emit(
                        "tool_call_result",
                        {
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        },
                    )
                    emit("hitl_task", hitl_task)
                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                    )
                    await save_proactive()
                    pending_interrupt = {
                        "task": hitl_task,
                        "tool_call_id": tool_call.get("id"),
                    }
                elif hitl_task:
                    all_tasks.append(hitl_task)
                    note = _hitl_proposal_note(observation)
                    feedback = _hitl_llm_feedback(hitl_task, note)
                    # Trimmed tool result so the chip resolves to "finished".
                    trimmed = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": feedback,
                    }
                    emit("tool_call_result", trimmed)
                    # Dedicated sentinel drives the interactive task card.
                    emit("hitl_task", hitl_task)
                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": feedback,
                        }
                    )
                    # Proposals are NOT data sources — no citation.
                    history.append(
                        ToolMessage(content=feedback, tool_call_id=tool_call["id"])
                    )
                    await save_proactive()
                else:
                    result_str = str(observation)
                    payload = {
                        "name": tool_name,
                        "args": tool_call["args"],
                        "result": result_str,
                    }
                    emit("tool_call_result", payload)
                    emit("citation", selected_tool.name)

                    all_tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": tool_name,
                            "args": tool_call["args"],
                            "result": result_str,
                        }
                    )
                    all_citations.append(selected_tool.name)

                    history.append(
                        ToolMessage(
                            content=str(observation),
                            tool_call_id=tool_call["id"],
                        )
                    )
            else:
                history.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found.",
                        tool_call_id=tool_call["id"],
                    )
                )
        emit("tool_call_finished", None)

        runtime["proactive_message"] = proactive_message
        return {
            "history": history,
            "all_tool_calls": all_tool_calls,
            "all_citations": all_citations,
            "all_tasks": all_tasks,
            "pending_tool_calls": [],
            "pending_interrupt": pending_interrupt,
        }

    async def await_user(state: ChatGraphState, config) -> Dict[str, Any]:
        """Pause the run for ask_user answers (Phase 3.3).

        The interrupt() call is the FIRST statement — this node must stay
        side-effect-free because LangGraph re-runs it from the top on resume
        (the resume value becomes interrupt()'s return value). The answers
        arrive as the ToolMessage responding to the ask_user tool_call_id.
        """
        pending = state["pending_interrupt"] or {}
        decision = interrupt(pending.get("task"))
        history = list(state["history"])
        tool_call_id = pending.get("tool_call_id")
        if tool_call_id:
            history.append(
                ToolMessage(content=str(decision), tool_call_id=tool_call_id)
            )
        return {"history": history, "pending_interrupt": None}

    def route_step(state: ChatGraphState) -> str:
        if not state["pending_tool_calls"]:
            return "finalize"
        return "tool_exec"

    def route_exec(state: ChatGraphState) -> str:
        if state["pending_interrupt"]:
            return "await_user"
        # Each graph iteration = one LLM call + its tool executions, matching
        # the loop engine's ``for i in range(max_iterations)``.
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        return "agent_step"

    def route_wait(state: ChatGraphState) -> str:
        if state["iteration"] >= state["max_iterations"]:
            return "finalize"
        return "agent_step"

    def route_start(state: ChatGraphState) -> str:
        # The loop engine with an exhausted cap never calls the LLM at all.
        if state["max_iterations"] <= 0:
            return "finalize"
        return "agent_step"

    async def finalize(state: ChatGraphState, config) -> Dict[str, Any]:
        writer = _stream_writer()
        runtime = config["configurable"]["runtime"]

        def emit(kind: str, data: Any) -> None:
            writer({"kind": kind, "data": data})

        # Clean break = agent_step produced no tool calls (iteration > 0).
        # A zero/exhausted cap (route_start) never ran the LLM — iteration 0
        # — which the loop engine reports as reached_max=True.
        clean_break = state["pending_tool_calls"] is None and state["iteration"] > 0
        session_id = state["session_id"]
        streaming = state["streaming"]
        chat_session_service = runtime["chat_session_service"]
        proactive_message = runtime["proactive_message"]
        # Crash-resume dedup: if the process died between the proactive save
        # and the final update, re-attach the proactive message by its task's
        # proposal_id so the resume writes an UPDATE, not a duplicate card.
        if (
            proactive_message is None
            and session_id
            and state["all_tasks"]
            and runtime.get("user_id")
            and runtime.get("tenant_id")
        ):
            try:
                proactive_message = await chat_session_service.find_message_by_proposal(
                    session_id=session_id,
                    proposal_id=state["all_tasks"][0].get("proposal_id"),
                    user_id=runtime["user_id"],
                    tenant_id=runtime["tenant_id"],
                )
            except Exception as e:
                logger.warning(f"Proactive-message re-lookup failed: {e}")

        # Final save: streaming always (matches prior behaviour);
        # non-streaming only on a clean no-tool-calls break.
        if (
            session_id
            and chat_session_service is not None
            and (streaming or clean_break)
        ):
            if proactive_message is not None:
                await chat_session_service.update_message_fields(
                    proactive_message,
                    content={"text": state["total_content"]},
                    tool_calls=state["all_tool_calls"],
                    citations=state["all_citations"],
                    tasks=state["all_tasks"] or None,
                )
            else:
                await chat_session_service.save_message(
                    session_id=session_id,
                    role="assistant",
                    content={"text": state["total_content"]},
                    tool_calls=state["all_tool_calls"],
                    citations=state["all_citations"],
                    tasks=state["all_tasks"] or None,
                )
        emit("done", not clean_break)
        return {}

    return (
        agent_step,
        tool_exec,
        await_user,
        route_step,
        route_exec,
        route_wait,
        route_start,
        finalize,
    )


def build_chat_graph(checkpointer: Optional[Any] = None):
    """Compile the chat-agent graph (per run; request-scoped handles ride in
    ``config["configurable"]``, never in the checkpointed state)."""
    (
        agent_step,
        tool_exec,
        await_user,
        route_step,
        route_exec,
        route_wait,
        route_start,
        finalize,
    ) = _chat_graph_nodes()
    def _flow_events(name: str, fn):
        """Emit family-vocabulary node events (additive, Phase 6.2) around a
        node body; the legacy sentinel events are untouched."""

        async def wrapped(state: ChatGraphState, config) -> Dict[str, Any]:
            writer = _stream_writer()
            writer(
                {
                    "kind": "flow_event",
                    "data": {"event": "node_started", "node": name},
                }
            )
            try:
                result = await fn(state, config)
            except Exception:
                writer(
                    {
                        "kind": "flow_event",
                        "data": {
                            "event": "node_finished",
                            "node": name,
                            "outcome": "failed",
                        },
                    }
                )
                raise
            writer(
                {
                    "kind": "flow_event",
                    "data": {
                        "event": "node_finished",
                        "node": name,
                        "outcome": "ok",
                    },
                }
            )
            return result

        wrapped.__name__ = name
        return wrapped

    builder = StateGraph(ChatGraphState)
    builder.set_node_defaults(
        retry_policy=RetryPolicy(
            max_attempts=2, retry_on=(ConnectionError, TimeoutError)
        )
    )
    builder.add_node("agent_step", _flow_events("agent_step", agent_step))
    builder.add_node(
        "tool_exec",
        _flow_events("tool_exec", tool_exec),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    builder.add_node("await_user", _flow_events("await_user", await_user))
    builder.add_node("finalize", _flow_events("finalize", finalize))
    builder.add_conditional_edges(START, route_start)
    builder.add_conditional_edges("agent_step", route_step)
    builder.add_conditional_edges("tool_exec", route_exec)
    builder.add_conditional_edges("await_user", route_wait)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _initial_state(
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    session_id: Optional[UUID],
) -> ChatGraphState:
    return {
        "streaming": streaming,
        "session_id": str(session_id) if session_id else None,
        "max_iterations": max_iterations,
        "history": history,
        "iteration": 0,
        "total_content": "",
        "all_tool_calls": [],
        "all_citations": [],
        "all_tasks": [],
        "pending_tool_calls": None,
        "pending_interrupt": None,
    }


def _make_config(
    session_id: Optional[UUID],
    checkpointer: Optional[Any],
    runtime: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if checkpointer is not None and session_id is None:
        raise ValueError("A checkpointer requires a session_id (thread_id).")
    configurable: Dict[str, Any] = {"runtime": runtime}
    if session_id is not None:
        configurable["thread_id"] = str(session_id)
    return {"configurable": configurable}


async def run_chat_graph(
    llm_with_tools,
    tools: List[Any],
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: Optional[UUID] = None,
    log_label: str = "AI Assistance",
    checkpointer: Optional[Any] = None,
    user_id: Optional[UUID] = None,
    tenant_id: Optional[UUID] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """Engine-selectable mirror of :func:`run_reasoning_loop`: consumes the
    graph's custom stream and re-emits the loop's ``(kind, data)`` events."""
    graph = build_chat_graph(checkpointer=checkpointer)
    runtime: Dict[str, Any] = {
        "llm_with_tools": llm_with_tools,
        "tools": tools,
        "chat_session_service": chat_session_service,
        "log_label": log_label,
        "proactive_message": None,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    config = _make_config(session_id, checkpointer, runtime)

    async def gen() -> AsyncIterator[Tuple[str, Any]]:
        # Phase 6.2 dual-emit: family vocabulary alongside the legacy
        # sentinel events (additive; stream_loop_as_sse gates the frames).
        yield ("flow_event", {"event": "flow_started", "flow": "chat"})
        done_seen = False
        try:
            async for mode, payload in graph.astream(
                _initial_state(
                    history,
                    max_iterations,
                    streaming=streaming,
                    session_id=session_id,
                ),
                config=config,
                stream_mode=["custom"],
            ):
                if (
                    mode == "custom"
                    and isinstance(payload, dict)
                    and "kind" in payload
                ):
                    if payload["kind"] == "done":
                        done_seen = True
                    yield (payload["kind"], payload["data"])
            if not done_seen:
                # The run paused at an ask_user interrupt (no finalize).
                yield ("flow_event", {"event": "interrupt"})
            else:
                yield ("flow_event", {"event": "flow_finished"})
        except Exception:
            yield ("flow_event", {"event": "flow_failed"})
            raise

    async for event in gen():
        yield event


def chat_engine_iter(
    llm_with_tools,
    tools: List[Any],
    history: List[Any],
    max_iterations: int,
    *,
    streaming: bool,
    chat_session_service=None,
    session_id: Optional[UUID] = None,
    log_label: str = "AI Assistance",
    user_id: Optional[UUID] = None,
    tenant_id: Optional[UUID] = None,
    checkpointer: Optional[Any] = None,
    engine: Optional[str] = None,
):
    """Select the chat engine (``loop`` | ``graph``).

    ``engine`` (resolved per tenant by the caller — tenant settings override
    the ``AI_AGENT_ENGINE`` env default) wins; unknown values fall back to
    the env default, which itself falls back to ``loop``.

    Returns an async iterator of the shared ``(kind, data)`` event vocabulary;
    callers are engine-agnostic. The graph engine attaches the lifespan-held
    checkpointer (thread_id = session id) for sessioned runs — ask_user
    interrupts need the durable thread; ``user_id``/``tenant_id`` enable the
    crash-resume dedup.
    """
    resolved = engine or settings.AI_AGENT_ENGINE
    if resolved == "graph":
        if checkpointer is None and session_id is not None:
            checkpointer = get_runtime_saver()
        return run_chat_graph(
            llm_with_tools,
            tools,
            history,
            max_iterations,
            streaming=streaming,
            chat_session_service=chat_session_service,
            session_id=session_id,
            log_label=log_label,
            checkpointer=checkpointer,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    return run_reasoning_loop(
        llm_with_tools,
        tools,
        history,
        max_iterations,
        streaming=streaming,
        chat_session_service=chat_session_service,
        session_id=session_id,
        log_label=log_label,
    )


async def has_pending_interrupt(session_id: UUID) -> bool:
    """True when the session's checkpointed graph run is paused at an
    ask_user interrupt (the /resume continuation should Command(resume=...)
    instead of starting a fresh loop turn)."""
    saver = get_runtime_saver()
    if saver is None:
        return False
    graph = build_chat_graph(checkpointer=saver)
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(session_id)}})
    if snapshot is None or not snapshot.tasks:
        return False
    return any(task.interrupts for task in snapshot.tasks)


async def resume_interrupted_chat_graph(
    session_id: UUID,
    resume_value: str,
    *,
    llm_with_tools,
    tools: List[Any],
    chat_session_service=None,
    log_label: str = "AI Assistance (resume)",
    user_id: Optional[UUID] = None,
    tenant_id: Optional[UUID] = None,
) -> Optional[AsyncIterator[Tuple[str, Any]]]:
    """Resume a run paused at an ask_user interrupt.

    Returns the familiar ``(kind, data)`` event iterator, or ``None`` when
    there is no pending interrupt (caller falls back to the loop engine's
    fresh-continuation turn). The summary the loop engine would have fed as
    the driving input arrives as the interrupt's return value — the
    ask_user ToolMessage — and the model continues the same conversation.
    The runtime handles are supplied fresh (they are never checkpointed).
    """
    saver = get_runtime_saver()
    if saver is None or not await has_pending_interrupt(session_id):
        return None
    graph = build_chat_graph(checkpointer=saver)
    runtime: Dict[str, Any] = {
        "llm_with_tools": llm_with_tools,
        "tools": tools,
        "chat_session_service": chat_session_service,
        "log_label": log_label,
        "proactive_message": None,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    config = _make_config(session_id, saver, runtime)

    async def gen() -> AsyncIterator[Tuple[str, Any]]:
        async for mode, payload in graph.astream(
            Command(resume=resume_value), config=config, stream_mode=["custom"]
        ):
            if mode == "custom" and isinstance(payload, dict) and "kind" in payload:
                yield (payload["kind"], payload["data"])

    return gen()

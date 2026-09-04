"""LangGraph graphs (ADR-0008): one module per flow, typed State.

Flows with ≥2 LLM calls in sequence, branching, iteration, or HITL
pause/resume live here. Nodes call the gateway (`get_llm`) — never models
directly. Single-call tasks stay on the gateway runner.

First flow lands in Phase 3.2 (`chat_graph.py`); the checkpoint store is
already scaffolded in :mod:`app.ai.graphs.checkpointer`.
"""

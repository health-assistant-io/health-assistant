"""Registry + context for the agentic-chat tools.

Each domain module under :mod:`app.ai.tools` defines a tool FACTORY decorated
with :func:`register_chat_tool`, which registers it under a domain name. The
factory receives a :class:`ToolContext` (the per-request db + tenant/patient/
exam scoping) and returns a list of LangChain ``@tool``-decorated callables
that close over that context — the same closure pattern the monolithic
``ChatbotTools.get_tools`` used, just split across domain modules.

:func:`app.ai.tools.get_tools` builds a ``ToolContext`` and concatenates the
output of every registered factory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """Per-request scoping bound to every tool. Mirrors the old
    ``ChatbotTools.__init__`` fields."""

    db: AsyncSession
    tenant_id: UUID
    patient_id: UUID
    examination_id: Optional[UUID] = None
    user_id: Optional[UUID] = None


# A factory maps a ToolContext -> list of LangChain tools (closures over ctx).
_TOOL_FACTORIES: "Dict[str, Callable[[ToolContext], List[Any]]]" = {}


def register_chat_tool(domain: str) -> Callable[[Callable[[ToolContext], List[Any]]], Callable[[ToolContext], List[Any]]]:
    """Decorator: register a tool factory under ``domain``.

    Usage in a domain module::

        @register_chat_tool("patient")
        def build(ctx: ToolContext):
            @tool
            async def get_patient_summary() -> str:
                ...  # close over ctx.db / ctx.tenant_id / ctx.patient_id
            return [get_patient_summary]
    """

    def _decorator(
        factory: Callable[[ToolContext], List[Any]],
    ) -> Callable[[ToolContext], List[Any]]:
        if domain in _TOOL_FACTORIES:
            raise ValueError(
                f"chat-tool domain {domain!r} already registered by "
                f"{_TOOL_FACTORIES[domain]!r}"
            )
        _TOOL_FACTORIES[domain] = factory
        return factory

    return _decorator


def get_factories() -> Dict[str, Callable[[ToolContext], List[Any]]]:
    """Snapshot of all registered domain factories (domain -> factory)."""
    return dict(_TOOL_FACTORIES)

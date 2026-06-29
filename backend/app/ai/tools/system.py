"""System / utility tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3).
"""
from datetime import datetime
from typing import Any, List

from langchain_core.tools import tool

from app.ai.tools.registry import ToolContext, register_chat_tool


@register_chat_tool("system")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_system_time() -> str:
        """Get the current system date and time. Use this to provide context for relative dates like 'today' or 'yesterday'."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return [get_system_time]

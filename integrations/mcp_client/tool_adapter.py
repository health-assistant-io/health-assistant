"""Convert MCP tools into LangChain ``StructuredTool`` objects.

The Health Assistant chat uses ``llm.bind_tools(tools)`` with standard
LangChain tool objects. This adapter wraps each MCP-discovered tool so the
existing reasoning loop, SSE markers, and ``ChatMessage.tool_calls``
persistence work unchanged.

Key behaviors:
- Namespacing: tools are renamed ``mcp__{instance_slug}__{original_name}``
  to avoid collisions with the built-in ``ChatbotTools`` and across MCP
  instances.
- The MCP tool's JSON-Schema ``inputSchema`` is converted to a Pydantic
  ``args_schema`` via ``pydantic.create_model``.
- Tool calls are routed through :class:`McpConnectionManager` with per-call
  timeout, concurrency cap, and result-size truncation (T4, T6).
- Tool names are sanitized to prevent namespace spoofing (no ``__`` in the
  original name).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from integrations.sdk.exceptions import IntegrationError

logger = logging.getLogger(__name__)

NAMESPACE_PREFIX = "mcp"
MAX_DESCRIPTION_LEN = 1024
MAX_TOOL_NAME_LEN = 64
# Hard cap on array/dict nesting depth when converting JSON Schema -> Pydantic.
_MAX_SCHEMA_DEPTH = 5


def sanitize_instance_slug(name: str, integration_id: UUID) -> str:
    """Derive a safe slug from the instance name (fallback to id prefix)."""
    raw = (name or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not raw:
        raw = str(integration_id).replace("-", "")[:8]
    return raw[:32]


def namespaced_name(instance_slug: str, original_name: str) -> str:
    """Return ``mcp__{slug}__{original}`` after validation."""
    if "__" in original_name:
        raise ValueError(
            f"MCP tool name '{original_name}' contains '__' which is reserved "
            "for namespacing. The server must rename this tool."
        )
    if not original_name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", original_name):
        raise ValueError(
            f"MCP tool name '{original_name}' is not a valid identifier."
        )
    full = f"{NAMESPACE_PREFIX}__{instance_slug}__{original_name}"
    if len(full) > MAX_TOOL_NAME_LEN:
        # Truncate the slug part, never the original (so routing still works).
        over = len(full) - MAX_TOOL_NAME_LEN
        slug = instance_slug[: max(1, len(instance_slug) - over)]
        full = f"{NAMESPACE_PREFIX}__{slug}__{original_name}"
    return full


def _truncate_description(desc: Optional[str]) -> str:
    if not desc:
        return ""
    desc = str(desc).strip()
    if len(desc) > MAX_DESCRIPTION_LEN:
        return desc[:MAX_DESCRIPTION_LEN] + "…[truncated]"
    return desc


def _json_schema_to_pydantic(name: str, schema: Optional[Dict[str, Any]], depth: int = 0):
    """Convert a JSON Schema object into a Pydantic model class.

    Falls back to a permissive model that accepts arbitrary kwargs if the
    schema is missing/invalid/too deeply nested — we never want to block a
    tool call because of a schema edge case.
    """
    from pydantic import Field, create_model

    if depth > _MAX_SCHEMA_DEPTH or not isinstance(schema, dict):
        return _permissive_model(name)

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        fields: Dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue
            py_type, default = _property_to_type(prop_schema, depth + 1)
            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=prop_schema.get("description")))
            else:
                fields[prop_name] = (
                    Optional[py_type],
                    Field(default=default, description=prop_schema.get("description")),
                )
        if not fields:
            return _permissive_model(name)
        try:
            return create_model(name, **fields)
        except Exception as e:
            logger.warning(f"Failed to create Pydantic model for {name}: {e}")
            return _permissive_model(name)

    return _permissive_model(name)


def _permissive_model(name: str):
    """A model that accepts any string-keyed args (escape hatch)."""
    from pydantic import Field, create_model
    from typing import Any as _Any

    return create_model(
        f"{name}_args",
        payload=(Optional[Dict[str, _Any]], Field(default=None, description="Tool arguments")),
    )


def _property_to_type(schema: Dict[str, Any], depth: int):
    """Map a JSON Schema property to a (python_type, default) tuple."""
    from typing import Any as _Any, Dict as _Dict, List as _List

    t = schema.get("type")
    if t == "string":
        return (str, schema.get("default") or "")
    if t == "integer":
        return (int, schema.get("default") or 0)
    if t == "number":
        return (float, schema.get("default") or 0.0)
    if t == "boolean":
        return (bool, schema.get("default") if schema.get("default") is not None else False)
    if t == "array":
        items = schema.get("items") or {}
        item_type, _ = _property_to_type(items, depth + 1) if isinstance(items, dict) else (_Any, None)
        return (_List[item_type], schema.get("default") or [])
    if t == "object":
        return (_Dict[str, _Any], schema.get("default") or {})
    # Unknown / union / null -> permissive
    return (_Any, None)


def _extract_text_result(result: Any, max_bytes: int) -> str:
    """Normalize a FastMCP ``CallToolResult`` into a bounded string for the LLM."""
    data = getattr(result, "data", None)
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    content = getattr(result, "content", None) or []

    parts: List[str] = []
    if data is not None:
        if isinstance(data, (dict, list)):
            parts.append(json.dumps(data, ensure_ascii=False, default=str))
        else:
            parts.append(str(data))

    if not parts and content:
        for block in content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            else:
                # Non-text content (image/audio). Surface a placeholder.
                parts.append(f"[non-text content: {type(block).__name__}]")

    if not parts:
        parts.append("" if not is_error else "[tool returned no content]")

    text = "\n".join(parts)
    if len(text.encode("utf-8")) > max_bytes:
        text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        text += f"\n…[truncated at {max_bytes} bytes]"
    if is_error:
        text = f"[MCP tool error] {text}"
    return text


def adapt_tool(
    integration,
    mcp_tool,
    instance_slug: str,
    connection_manager,
) -> Any:
    """Wrap a single MCP ``Tool`` as a LangChain ``StructuredTool``.

    The returned tool is async (``coroutine=...``) and bound to the given
    integration via the connection manager.
    """
    from langchain_core.tools import StructuredTool

    original_name = mcp_tool.name
    namespaced = namespaced_name(instance_slug, original_name)
    description = _truncate_description(mcp_tool.description)
    if not description:
        description = f"MCP tool '{original_name}' from instance '{instance_slug}'."

    args_schema = _json_schema_to_pydantic(f"{namespaced}_args", mcp_tool.inputSchema)

    integration_id = integration.id
    config = integration.user_config or {}
    enabled = set(config.get("enabled_tools") or [])
    disabled = set(config.get("disabled_tools") or [])
    include_tags = set(config.get("include_tags") or [])
    exclude_tags = set(config.get("exclude_tags") or [])
    from app.core.config import settings

    max_bytes = int(
        config.get("tool_result_max_bytes") or settings.MCP_TOOL_RESULT_MAX_BYTES
    )

    # Tool-level filters.
    if enabled and original_name not in enabled:
        return None
    if original_name in disabled:
        return None
    tags = set(getattr(mcp_tool, "tags", None) or [])
    # Some FastMCP versions expose annotations instead of tags.
    annotations = getattr(mcp_tool, "annotations", None)
    if annotations and not tags:
        # Annotations is a ToolAnnotations struct; nothing useful for tag
        # filtering, but be defensive.
        tags = set()
    if include_tags and not (tags & include_tags):
        return None
    if exclude_tags and (tags & exclude_tags):
        return None

    async def _arun(**kwargs) -> str:
        # ``payload`` is the permissive-model fallback; if a real schema was
        # built, kwargs holds the typed fields.
        if list(kwargs.keys()) == ["payload"] and kwargs["payload"] is not None:
            arguments = kwargs["payload"]
        else:
            arguments = {k: v for k, v in kwargs.items() if v is not None}
        try:
            result = await connection_manager.call_tool(integration, original_name, arguments)
        except IntegrationError as e:
            return f"[MCP tool error] {e}"
        return _extract_text_result(result, max_bytes)

    return StructuredTool.from_function(
        coroutine=_arun,
        name=namespaced,
        description=description,
        args_schema=args_schema,
    )


def filter_and_adapt_tools(
    integration,
    mcp_tools: List[Any],
    connection_manager,
) -> List[Any]:
    """Adapt a list of MCP tools, dropping any that fail validation/filtering."""
    instance_slug = sanitize_instance_slug(
        getattr(integration, "instance_name", None) or "", integration.id
    )
    adapted: List[Any] = []
    for t in mcp_tools:
        try:
            tool = adapt_tool(integration, t, instance_slug, connection_manager)
        except Exception as e:
            logger.warning(
                f"Skipping MCP tool {getattr(t, 'name', '?')} on integration "
                f"{integration.id}: {e}"
            )
            continue
        if tool is not None:
            adapted.append(tool)
    return adapted

"""MCP Client provider.

This integration does not produce FHIR observations. It exposes MCP server
tools to the chat assistant via the SDK's :meth:`supports_tools` /
:meth:`get_tools` contract (see ``integrations.sdk.base``). The platform
tool aggregator discovers it generically — no ``mcp_client``-specific code
in the chat service.

``pull_data`` is a no-op so the background sync loop skips this domain.
Tokenless routes (webhook / api proxy) are intentionally closed for tool
invocation (T5) — all tool calls go through the authenticated chat path.
"""
from typing import Any, Dict, List

from integrations.sdk import BaseHealthProvider, kv_block, list_block, table_block
from app.models.user_integration import UserIntegration
from app.schemas.fhir.observation import ObservationCreate


class McpClientProvider(BaseHealthProvider):
    domain = "mcp_client"

    # --- Tool exposure (SDK contract) ---

    def supports_tools(self) -> bool:
        return True

    async def get_tools(self, integration: UserIntegration) -> List[Any]:
        """Return LangChain tools for this MCP instance.

        Swallows per-instance errors and returns ``[]`` on failure so one
        bad server doesn't break the whole chat turn.
        """
        from integrations.mcp_client.connection_manager import mcp_connection_manager
        from integrations.mcp_client.tool_adapter import filter_and_adapt_tools

        try:
            mcp_tools = await mcp_connection_manager.list_tools(integration)
        except Exception as e:
            self.logger.warning(
                f"MCP list_tools failed for instance {integration.id} "
                f"({integration.instance_name}): {e}"
            )
            return []
        return filter_and_adapt_tools(integration, mcp_tools, mcp_connection_manager)

    async def close(self):
        """Close the shared httpx client + all live MCP subprocesses."""
        await super().close()
        from integrations.mcp_client.connection_manager import mcp_connection_manager

        await mcp_connection_manager.disconnect_all()

    # --- Data flow (no-op) ---

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        """No-op. MCP is request-driven, not poll-driven."""
        return []

    async def handle_api_request(
        self, integration: UserIntegration, path: str, method: str, request: Any
    ) -> Dict[str, Any]:
        """Only ``GET /status`` is exposed. Tool invocation is refused.

        The api-proxy route is tokenless (UUID-as-secret). Exposing tool
        calls over it would let anyone with the UUID run arbitrary MCP
        tools. All tool calls must go through the authenticated chat path
        (``/ai-assistance/stream``) which enforces user_id + tenant_id.
        """
        if path == "status" and method == "GET":
            from integrations.mcp_client.connection_manager import mcp_connection_manager

            return await mcp_connection_manager.health(integration)
        raise NotImplementedError(
            "Tool invocation over the tokenless API proxy is disabled for MCP. "
            "Use the chat assistant (authenticated) to call MCP tools."
        )

    # --- Custom actions ---

    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "test_connection", "label": "Test Connection", "style": "primary"},
            {"id": "list_tools", "label": "List Tools", "style": "default"},
            {"id": "restart_connection", "label": "Restart Connection", "style": "warning"},
        ]

    async def execute_custom_action(
        self, integration: UserIntegration, action_id: str, **kwargs
    ) -> Dict[str, Any]:
        from integrations.mcp_client.connection_manager import mcp_connection_manager

        if action_id == "test_connection":
            status = await mcp_connection_manager.health(integration)
            if status["status"] == "connected":
                return {
                    "message": (
                        f"Connected via {status['transport']}. "
                        f"{status['tools']} tool(s) available."
                    ),
                    "results": [
                        kv_block("Connection", {
                            "Status": "Connected",
                            "Transport": status["transport"],
                            "Tools available": status["tools"],
                            "Instance": integration.instance_name or integration.id,
                        })
                    ],
                }
            return {
                "message": f"Connection failed: {status.get('error', 'unknown')}",
                "results": [
                    kv_block("Connection", {
                        "Status": "Error",
                        "Error": status.get("error", "unknown"),
                        "Instance": integration.instance_name or integration.id,
                    })
                ],
            }

        if action_id == "list_tools":
            try:
                tools = await mcp_connection_manager.list_tools(integration)
                names = [t.name for t in tools]
                rows = [
                    [t.name, (t.description or "").split("\n")[0][:120]]
                    for t in tools
                ]
                return {
                    "message": f"Discovered {len(names)} tool(s).",
                    "results": [
                        kv_block("Summary", {
                            "Total tools": len(names),
                            "Instance": integration.instance_name or integration.id,
                        }),
                        table_block("Available Tools", ["Name", "Description"], rows)
                        if rows
                        else list_block("Available Tools", []),
                    ],
                }
            except Exception as e:
                return {
                    "message": f"Failed to list tools: {e}",
                    "results": [
                        kv_block("Error", {"Detail": str(e)})
                    ],
                }

        if action_id == "restart_connection":
            await mcp_connection_manager.disconnect(integration.id)
            return {"message": "Connection closed. It will reconnect on next use."}

        raise NotImplementedError(f"Action '{action_id}' is not supported by mcp_client.")

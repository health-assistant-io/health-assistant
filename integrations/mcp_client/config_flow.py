"""Configuration flow for the MCP Client integration.

The schema uses a ``transport`` enum (stdio | http | sse) plus transport-specific
fields. The frontend renders conditionally based on the ``depends_on`` keyword
(see ConfigFlowModal.tsx). Secret fields are tagged ``x-secret: true`` and
encrypted by the endpoint before storage.
"""
from typing import Any, Dict

from integrations.sdk import BaseConfigFlow
from integrations.mcp_client.security import (
    validate_http_url,
    validate_stdio_command,
)


TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"
TRANSPORT_SSE = "sse"


class McpClientConfigFlow(BaseConfigFlow):
    domain = "mcp_client"

    def __init__(self):
        # Read the per-user cap from settings at construction time. The
        # platform endpoint enforces ``max_instances_per_user`` generically.
        from app.core.config import settings

        self.max_instances_per_user = settings.MCP_MAX_SERVERS_PER_USER

    def get_secret_fields(self):
        """Fields encrypted at rest + masked on read by the SDK base."""
        return ["env", "headers", "auth_token"]

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure MCP Server",
            "description": (
                "Connect to a Model Context Protocol server. Tools exposed by "
                "the server will be available to the Assistant in chat."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "description": "A label for this MCP server (e.g. 'GitHub MCP').",
                        "default": "MCP Server",
                    },
                    "transport": {
                        "type": "string",
                        "title": "Transport",
                        "enum": [TRANSPORT_STDIO, TRANSPORT_HTTP, TRANSPORT_SSE],
                        "enum_descriptions": {
                            TRANSPORT_STDIO: "Local subprocess (e.g. npx, python). Server runs on this machine.",
                            TRANSPORT_HTTP: "Streamable HTTP (recommended for remote servers).",
                            TRANSPORT_SSE: "Server-Sent Events (legacy remote servers).",
                        },
                        "default": TRANSPORT_HTTP,
                    },
                    # --- STDIO fields ---
                    "command": {
                        "type": "string",
                        "title": "Command",
                        "description": "Bare command from the allowlist (e.g. 'npx', 'python').",
                        "depends_on": {"field": "transport", "value": TRANSPORT_STDIO},
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Arguments",
                        "description": "Command-line arguments (one per line).",
                        "default": [],
                        "depends_on": {"field": "transport", "value": TRANSPORT_STDIO},
                    },
                    "env": {
                        "type": "object",
                        "x-format": "key-value",
                        "title": "Environment Variables",
                        "description": "Secret env vars for the subprocess (encrypted at rest).",
                        "default": {},
                        "x-secret": True,
                        "depends_on": {"field": "transport", "value": TRANSPORT_STDIO},
                    },
                    "cwd": {
                        "type": "string",
                        "title": "Working Directory",
                        "description": "Optional absolute path.",
                        "depends_on": {"field": "transport", "value": TRANSPORT_STDIO},
                    },
                    "keep_alive": {
                        "type": "boolean",
                        "title": "Keep Subprocess Alive Between Calls",
                        "default": True,
                        "depends_on": {"field": "transport", "value": TRANSPORT_STDIO},
                    },
                    # --- HTTP / SSE common fields ---
                    "url": {
                        "type": "string",
                        "title": "Server URL",
                        "description": "https://... (http:// requires MCP_ALLOW_INSECURE_HTTP=True).",
                        "depends_on": {"field": "transport", "value": TRANSPORT_HTTP},
                        "depends_on_any": [{"field": "transport", "value": TRANSPORT_SSE}],
                    },
                    "auth_token": {
                        "type": "string",
                        "format": "password",
                        "title": "Bearer Token",
                        "description": "Optional. Sent as Authorization: Bearer <token>.",
                        "x-secret": True,
                        "depends_on_any": [
                            {"field": "transport", "value": TRANSPORT_HTTP},
                            {"field": "transport", "value": TRANSPORT_SSE},
                        ],
                    },
                    "headers": {
                        "type": "object",
                        "x-format": "key-value",
                        "title": "Extra Headers",
                        "description": "Custom HTTP headers (encrypted at rest).",
                        "default": {},
                        "x-secret": True,
                        "depends_on_any": [
                            {"field": "transport", "value": TRANSPORT_HTTP},
                            {"field": "transport", "value": TRANSPORT_SSE},
                        ],
                    },
                    "verify_ssl": {
                        "type": "boolean",
                        "title": "Verify SSL Certificate",
                        "default": True,
                        "depends_on_any": [
                            {"field": "transport", "value": TRANSPORT_HTTP},
                            {"field": "transport", "value": TRANSPORT_SSE},
                        ],
                    },
                    "ca_bundle_path": {
                        "type": "string",
                        "title": "CA Bundle Path",
                        "description": "Optional path to a CA bundle for self-signed servers.",
                        "depends_on_any": [
                            {"field": "transport", "value": TRANSPORT_HTTP},
                            {"field": "transport", "value": TRANSPORT_SSE},
                        ],
                    },
                    # --- Tool filtering (all transports) ---
                    "enabled_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Enabled Tools (allowlist)",
                        "description": "If non-empty, only these tool names are exposed. One per line.",
                        "default": [],
                    },
                    "disabled_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Disabled Tools (blocklist)",
                        "description": "Tool names to hide from the Assistant. One per line.",
                        "default": [],
                    },
                    "include_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Include Tags",
                        "description": "Only expose tools with at least one of these tags.",
                        "default": [],
                    },
                    "exclude_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "title": "Exclude Tags",
                        "description": "Hide tools with any of these tags.",
                        "default": [],
                    },
                    "request_timeout": {
                        "type": "number",
                        "title": "Per-Call Timeout (seconds)",
                        "default": 30,
                        "minimum": 1,
                        "maximum": 600,
                    },
                    "tool_result_max_bytes": {
                        "type": "integer",
                        "title": "Max Tool Result Size (bytes)",
                        "default": 65536,
                        "minimum": 256,
                        "maximum": 1048576,
                    },
                },
                "required": ["instance_name", "transport"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        if not user_input:
            raise ValueError("No configuration provided.")
        transport = user_input.get("transport")
        if transport not in (TRANSPORT_STDIO, TRANSPORT_HTTP, TRANSPORT_SSE):
            raise ValueError("Invalid transport. Choose stdio, http, or sse.")

        if not user_input.get("instance_name") or not str(user_input["instance_name"]).strip():
            raise ValueError("Instance name is required.")

        if transport == TRANSPORT_STDIO:
            ok, reason = validate_stdio_command(
                command=user_input.get("command", ""),
                args=user_input.get("args") or [],
                cwd=user_input.get("cwd"),
            )
            if not ok:
                raise ValueError(reason)
        else:
            ok, reason = validate_http_url(user_input.get("url", ""))
            if not ok:
                raise ValueError(reason)

        # Per-user instance cap is enforced by the endpoint (it has DB access).

        # Normalize array/object defaults so we don't store nulls.
        for k in ("args", "enabled_tools", "disabled_tools", "include_tags", "exclude_tags"):
            if user_input.get(k) is None:
                user_input[k] = []
        for k in ("env", "headers"):
            if user_input.get(k) is None:
                user_input[k] = {}

        timeout = user_input.get("request_timeout")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ValueError("request_timeout must be a positive number.")

        max_bytes = user_input.get("tool_result_max_bytes")
        if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 256):
            raise ValueError("tool_result_max_bytes must be >= 256.")

        return user_input

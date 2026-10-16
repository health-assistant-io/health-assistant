"""MCP client construction — the AI-layer factory (ADR-0012 / plan-32 D24).

Every ``fastmcp`` import in health lives here (alignment-gate R2: MCP
libraries are AI-layer-only). The ``mcp_client`` bundled extension
(``app/extensions/builtin/mcp_client/``) owns lifecycle, allowlists and
security validation, and delegates raw client construction to
:func:`client_for_spec` — the career ``ai/mcp_client.client_for_spec``
pattern, ratified family-wide by D24 rule 1 (AI-layer MCP adapters stay
in ``ai/``).

Construction stays lazy: importing this module costs nothing until a
client is actually built, and a missing ``fastmcp`` install surfaces as
the charter-typed ``not-ready`` error.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def client_for_spec(
    *,
    transport: str,
    url: str = "",
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    keep_alive: bool = True,
    headers: dict[str, str] | None = None,
    auth_token: str | None = None,
    verify: bool | Any = True,
    timeout_seconds: float = 30.0,
) -> tuple[Any, str]:
    """Build a ``fastmcp.Client`` for a validated connection spec.

    Returns ``(client, transport_kind)``. Security validation (command
    allowlist, URL guard, SSL policy) happens at the caller — the
    extension's security module — before anything reaches this factory;
    this function is pure construction.
    """
    try:
        from fastmcp import Client
        from fastmcp.client.auth import BearerAuth
        from fastmcp.client.transports import (
            SSETransport,
            StdioTransport,
            StreamableHttpTransport,
        )
    except ImportError as e:
        raise RuntimeError("fastmcp is not installed. Add it to backend/requirements.txt.") from e

    if transport == "stdio":
        transport_obj = StdioTransport(
            command=command,
            args=list(args or []),
            env={k: str(v) for k, v in env.items()} if env else None,
            cwd=cwd,
            keep_alive=keep_alive,
        )
        return Client(transport_obj, timeout=timeout_seconds), "stdio"

    if transport in ("http", "sse"):
        auth = BearerAuth(auth_token) if auth_token else None
        if transport == "http":
            transport_obj = StreamableHttpTransport(
                url=url, headers=headers or None, auth=auth, verify=verify
            )
        else:
            transport_obj = SSETransport(url=url, headers=headers or None, auth=auth, verify=verify)
        return Client(transport_obj, timeout=timeout_seconds), transport

    raise ValueError(f"Unknown transport: {transport}")

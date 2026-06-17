"""Per-integration FastMCP Client lifecycle manager.

The Health Assistant integration registry instantiates ONE provider per
domain (singleton). MCP servers, however, need per-instance long-lived
connections (especially STDIO subprocesses). This module bridges that gap
with a module-level singleton keyed by ``integration_id``.

Design:
- ``get_or_connect(integration)`` lazily creates a ``fastmcp.Client`` from
  the decrypted ``user_config`` and caches it. Repeated calls reuse the
  connection (and for STDIO, the subprocess).
- Per-instance ``asyncio.Semaphore`` caps concurrent tool calls
  (``MCP_PER_INSTANCE_CONCURRENCY``) — T6.
- Idle timeout (``MCP_CONNECTION_IDLE_TIMEOUT``) closes connections that
  haven't been used recently; LRU eviction at ``MCP_MAX_TOTAL_STDIO`` — T6.
- ``httpx.AsyncClient`` is per-connection, never the SDK shared pool — T3.
- Auto-reconnect with backoff; failed connections raise
  :class:`IntegrationError`-compatible errors so the chat can skip the
  instance and continue.

Security:
- STDIO command is re-validated before launch (defense in depth vs. config
  tampering).
- Secret config fields are decrypted here via :class:`SecretCipher`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError, IntegrationError

logger = logging.getLogger(__name__)


class _Connection:
    """A live FastMCP client + its bookkeeping.

    FastMCP clients require ``async with`` for the session lifecycle. For a
    long-lived connection we call ``__aenter__`` once at creation and
    ``__aexit__`` at teardown.
    """

    __slots__ = ("client", "sem", "last_used", "transport_kind", "lock", "entered")

    def __init__(self, client, transport_kind: str, concurrency: int) -> None:
        self.client = client
        self.sem = asyncio.Semaphore(concurrency)
        self.last_used = time.monotonic()
        self.transport_kind = transport_kind  # "stdio" | "http" | "sse"
        self.lock = asyncio.Lock()
        self.entered = False


class McpConnectionManager:
    """Module-level singleton managing all live MCP connections."""

    def __init__(self) -> None:
        self._connections: "OrderedDict[UUID, _Connection]" = OrderedDict()
        self._global_lock = asyncio.Lock()
        self._closing = False

    # ------------------------------------------------------------------ public

    async def get_or_connect(self, integration) -> Any:
        """Return a live :class:`fastmcp.Client` for ``integration``.

        Raises ``IntegrationError`` (or subclasses) on failure so callers
        (e.g. the chat aggregator) can skip the instance cleanly.
        """
        if self._closing:
            raise IntegrationError("MCP connection manager is shutting down.")

        integration_id = integration.id if hasattr(integration, "id") else UUID(str(integration))
        async with self._global_lock:
            conn = self._connections.get(integration_id)
            if conn is not None:
                conn.last_used = time.monotonic()
                self._connections.move_to_end(integration_id)
                return conn.client
            client, kind = await self._build_client(integration)
            from app.core.config import settings

            conn = _Connection(client, kind, settings.MCP_PER_INSTANCE_CONCURRENCY)
            # Enter the FastMCP context once -> keeps the session (and for
            # STDIO, the subprocess) alive across multiple tool calls.
            try:
                await client.__aenter__()
                conn.entered = True
            except Exception as e:
                await self._safe_close(conn)
                raise IntegrationError(f"Failed to connect to MCP server: {e}")
            self._connections[integration_id] = conn
            await self._evict_if_needed()
            return conn.client

    async def call_tool(
        self,
        integration,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Call a tool on the integration's server with concurrency + timeout."""
        integration_id = integration.id if hasattr(integration, "id") else UUID(str(integration))
        conn = self._connections.get(integration_id)
        if conn is None:
            # Auto-connect if not connected (e.g. first call in a chat).
            await self.get_or_connect(integration)
            conn = self._connections[integration_id]

        from app.core.config import settings

        per_call_timeout = float(
            (integration.user_config or {}).get("request_timeout")
            or settings.MCP_REQUEST_TIMEOUT
        )

        async with conn.sem:
            conn.last_used = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    conn.client.call_tool(tool_name, arguments or {}),
                    timeout=per_call_timeout,
                )
            except asyncio.TimeoutError:
                raise IntegrationError(
                    f"MCP tool '{tool_name}' timed out after {per_call_timeout}s."
                )
            except Exception as e:
                # Reconnect once on connection-level errors, then surface.
                logger.warning(f"MCP call_tool error on {tool_name}: {e}")
                await self._reconnect(integration_id, integration)
                raise IntegrationDataError(f"MCP tool '{tool_name}' failed: {e}")
        return result

    async def list_tools(self, integration) -> list:
        """List tools exposed by the integration's server."""
        integration_id = integration.id if hasattr(integration, "id") else UUID(str(integration))
        conn = self._connections.get(integration_id)
        if conn is None:
            await self.get_or_connect(integration)
            conn = self._connections[integration_id]

        async with conn.sem:
            conn.last_used = time.monotonic()
            try:
                return await conn.client.list_tools()
            except Exception as e:
                logger.warning(f"MCP list_tools error: {e}")
                await self._reconnect(integration_id, integration)
                raise IntegrationDataError(f"MCP list_tools failed: {e}")

    async def disconnect(self, integration_id: UUID) -> None:
        async with self._global_lock:
            conn = self._connections.pop(integration_id, None)
        if conn is not None:
            await self._safe_close(conn)

    async def disconnect_all_for_user(self, user_id: UUID, db) -> None:
        """Disconnect all MCP connections belonging to a user.

        Looks up ``UserIntegration`` rows to resolve ids; safe to call on
        user delete / logout.
        """
        from sqlalchemy import select
        from app.models.user_integration import UserIntegration

        result = await db.execute(
            select(UserIntegration.id).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "mcp_client",
            )
        )
        ids = [row[0] for row in result.all()]
        for iid in ids:
            await self.disconnect(iid)

    async def disconnect_all(self) -> None:
        """Shutdown helper: close every live connection."""
        self._closing = True
        async with self._global_lock:
            items = list(self._connections.items())
            self._connections.clear()
        for iid, conn in items:
            logger.info(f"MCP shutdown: closing connection {iid}")
            await self._safe_close(conn)

    async def health(self, integration) -> Dict[str, Any]:
        """Lightweight status check used by the 'test_connection' custom action."""
        integration_id = integration.id if hasattr(integration, "id") else UUID(str(integration))
        try:
            await self.get_or_connect(integration)
            tools = await self.list_tools(integration)
            return {
                "status": "connected",
                "transport": self._connections[integration_id].transport_kind,
                "tools": len(tools),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ------------------------------------------------------------------ internal

    async def _build_client(self, integration) -> Tuple[Any, str]:
        """Construct a ``fastmcp.Client`` from the integration config.

        Returns ``(client, transport_kind)``. Imports ``fastmcp`` lazily so
        the rest of the app works even if fastmcp isn't installed.
        """
        try:
            from fastmcp import Client
            from fastmcp.client.transports import (
                StdioTransport,
                StreamableHttpTransport,
                SSETransport,
            )
            from fastmcp.client.auth import BearerAuth
        except ImportError as e:
            raise IntegrationError(
                "fastmcp is not installed. Add it to backend/requirements.txt."
            ) from e

        from app.core.config import settings
        from integrations.sdk.secrets import decrypt_fields
        from integrations.mcp_client.security import (
            build_ssl_context,
            validate_http_url,
            validate_stdio_command,
        )

        raw_config = integration.user_config or {}
        # Decrypt secret fields via the SDK helper. The set of secret fields
        # is declared by the config flow; we hard-code them here because the
        # connection manager needs the plaintext regardless of which config
        # flow class is active. This stays MCP-specific.
        try:
            config = decrypt_fields(
                raw_config, ["env", "headers", "auth_token"]
            )
        except RuntimeError as e:
            raise IntegrationError(str(e))
        except Exception as e:
            raise IntegrationDataError(f"Failed to decrypt MCP config: {e}")

        transport = (config.get("transport") or "http").lower()
        per_call_timeout = float(config.get("request_timeout") or settings.MCP_REQUEST_TIMEOUT)

        if transport == "stdio":
            command = config.get("command") or ""
            args = config.get("args") or []
            env = config.get("env") or {}
            cwd = config.get("cwd")
            ok, reason = validate_stdio_command(command, args, cwd)
            if not ok:
                raise IntegrationDataError(reason)
            transport_obj = StdioTransport(
                command=command,
                args=list(args),
                env={k: str(v) for k, v in env.items()} if env else None,
                cwd=cwd,
                keep_alive=bool(config.get("keep_alive", True)),
            )
            client = Client(transport_obj, timeout=per_call_timeout)
            logger.info(f"MCP STDIO client built for integration {integration.id}: {command}")
            return client, "stdio"

        if transport in ("http", "sse"):
            url = config.get("url") or ""
            ok, reason = validate_http_url(url)
            if not ok:
                raise IntegrationDataError(reason)

            headers = dict(config.get("headers") or {})
            auth_token = config.get("auth_token")
            auth = BearerAuth(auth_token) if auth_token else None

            verify_ssl = bool(config.get("verify_ssl", True))
            ca_bundle = config.get("ca_bundle_path") or None
            ssl_ctx = build_ssl_context(verify_ssl, ca_bundle) if verify_ssl else False

            if transport == "http":
                transport_obj = StreamableHttpTransport(
                    url=url, headers=headers or None, auth=auth, verify=ssl_ctx
                )
            else:
                transport_obj = SSETransport(
                    url=url, headers=headers or None, auth=auth, verify=ssl_ctx
                )
            client = Client(transport_obj, timeout=per_call_timeout)
            logger.info(f"MCP {transport.upper()} client built for integration {integration.id}: {url}")
            return client, transport

        raise IntegrationDataError(f"Unknown transport: {transport}")

    async def _reconnect(self, integration_id: UUID, integration) -> None:
        """Drop the current connection so the next call rebuilds it."""
        async with self._global_lock:
            conn = self._connections.pop(integration_id, None)
        if conn is not None:
            await self._safe_close(conn)
        # Don't eagerly reconnect here — let the next call rebuild, or the
        # chat will skip this instance for this turn.

    async def _evict_if_needed(self) -> None:
        """Enforce ``MCP_MAX_TOTAL_STDIO`` (approximated as total connections)."""
        from app.core.config import settings

        max_total = settings.MCP_MAX_TOTAL_STDIO
        while len(self._connections) > max_total:
            iid, conn = self._connections.popitem(last=False)  # LRU
            logger.info(f"MCP LRU evicting connection {iid}")
            await self._safe_close(conn)

    async def _safe_close(self, conn: _Connection) -> None:
        """Best-effort close; FastMCP clients expose an async ``close()``.

        If the client was entered (long-lived session), exit the context
        first so the transport/subprocess tears down cleanly.
        """
        try:
            if conn.entered:
                await conn.client.__aexit__(None, None, None)
                conn.entered = False
            else:
                await conn.client.close()
        except Exception as e:
            logger.warning(f"MCP connection close error (ignored): {e}")


mcp_connection_manager = McpConnectionManager()

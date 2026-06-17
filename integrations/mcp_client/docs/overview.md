# Overview

The **MCP Client** integration lets a user connect to any [Model Context Protocol](https://modelcontextprotocol.io) server and expose that server's tools to the Health Assistant chat assistant.

## What it does

- Discovers tools from an MCP server (STDIO / Streamable HTTP / SSE).
- Wraps each tool as a standard LangChain tool, namespaced as `mcp__<instance>__<tool>`.
- The chat assistant can call those tools alongside the built-in clinical tools (`get_biomarker_history`, etc.).
- One user can have many MCP Client instances — one per MCP server.

## What it does NOT do

- It does **not** produce FHIR observations or biomarker data. It is purely a tool provider for the assistant.
- It does **not** poll on a schedule. Connections are lazy (opened on first chat use) and closed after an idle timeout.
- It does **not** expose tool calls over the tokenless API proxy. All tool calls go through the authenticated chat endpoint.

## Transports

| Transport | Use case | Notes |
|-----------|----------|-------|
| `stdio`   | Local subprocess (e.g. `npx -y @modelcontextprotocol/server-github`) | Command must be in the admin allowlist (`MCP_STDIO_ALLOWED_COMMANDS`). |
| `http`    | Remote Streamable HTTP server (recommended for remote) | `https://` enforced unless `MCP_ALLOW_INSECURE_HTTP=True`. |
| `sse`     | Legacy Server-Sent Events remote server | Same as HTTP. |

## Tool filtering

Per-instance config supports:
- `enabled_tools` / `disabled_tools` — explicit allow/block list by tool name.
- `include_tags` / `exclude_tags` — tag-based filtering (if the server tags tools).

A global cap `INTEGRATION_MAX_TOOLS_PER_SESSION` bounds how many MCP tools are exposed in a single chat session.

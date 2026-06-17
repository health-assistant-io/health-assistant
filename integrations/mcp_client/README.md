# MCP Client

Connect to [Model Context Protocol](https://modelcontextprotocol.io) servers and expose their tools to the Health Assistant chat.

See `docs/` for the full guide. Multi-instance: one `MCP Client` integration per MCP server; a user can connect to many.

## Quick start

1. Admin: enable the `mcp_client` integration (`/admin/system/integrations`).
2. Set `INTEGRATION_SECRET_KEY` in `.env` (Fernet key — see [Environment Variables](docs/environment.md) for all settings).
3. Pick a transport:
   - **STDIO** — local subprocess, e.g. `command: npx`, `args: ["-y", "@modelcontextprotocol/server-github"]`, `env: { GITHUB_TOKEN: "..." }`.
   - **HTTP / SSE** — remote server, e.g. `url: https://api.example.com/mcp`, `auth_token: "..."`.
4. Save. Use **Test Connection** to verify, **List Tools** to see what's exposed.
5. In chat, the assistant can now call those tools (namespaced as `mcp__<instance>__<tool>`).

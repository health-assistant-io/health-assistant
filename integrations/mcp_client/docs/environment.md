# Environment Variables

All configuration for the MCP Client integration is done via environment variables in the root `.env` file. Run `python scripts/setup_env.py` to generate one, or copy `.env.example` and adjust as needed.

## Required

| Variable | Description | Example |
|----------|-------------|---------|
| `INTEGRATION_SECRET_KEY` | Fernet key (base64 32 bytes) used to encrypt secret config fields (`env`, `headers`, `auth_token`) at rest. Required if the integration is enabled — saving config fails fast with a 400 if it's missing. | `ZG1h...==` |

Generate with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## STDIO security

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_STDIO_ALLOWED_COMMANDS` | `npx,uvx,python,python3,node` | Comma-separated allowlist of bare commands users may launch via STDIO. Users cannot run commands outside this list. (Threat T1) |
| `MCP_ALLOW_INSECURE_HTTP` | `False` | If `True`, allow `http://` URLs for HTTP/SSE transports. **Not recommended in production** — use `https://`. (Threat T7) |

## Resource limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_MAX_SERVERS_PER_USER` | `5` | Maximum MCP server instances a single user can configure. Enforced on create. |
| `MCP_MAX_TOTAL_STDIO` | `20` | Maximum concurrent live connections (across all users on this backend). LRU eviction when exceeded. (Threat T6) |
| `MCP_PER_INSTANCE_CONCURRENCY` | `4` | Maximum concurrent tool calls per MCP instance. Prevents one chat from saturating a server. (Threat T6) |
| `MCP_REQUEST_TIMEOUT` | `30` | Default per-tool-call timeout in seconds. Overridable per-instance via `request_timeout` in config. (Threat T6) |
| `MCP_TOOL_RESULT_MAX_BYTES` | `65536` | Maximum size of a tool result returned to the LLM. Larger results are truncated with a marker. (Threat T4) |
| `MCP_CONNECTION_IDLE_TIMEOUT` | `900` | Seconds of inactivity before a connection is closed. Reconnected on next use. |
| `INTEGRATION_MAX_TOOLS_PER_SESSION` | `20` | Maximum total integration tools exposed in a single chat turn (across all of the user's MCP instances). |

## Summary table (for quick copy)

```env
# Required
INTEGRATION_SECRET_KEY=

# STDIO security
MCP_STDIO_ALLOWED_COMMANDS=npx,uvx,python,python3,node
MCP_ALLOW_INSECURE_HTTP=False

# Resource limits
MCP_MAX_SERVERS_PER_USER=5
MCP_MAX_TOTAL_STDIO=20
MCP_PER_INSTANCE_CONCURRENCY=4
MCP_REQUEST_TIMEOUT=30
MCP_TOOL_RESULT_MAX_BYTES=65536
MCP_CONNECTION_IDLE_TIMEOUT=900
INTEGRATION_MAX_TOOLS_PER_SESSION=20
```

## Notes

- **`INTEGRATION_SECRET_KEY`** is a platform-level setting (used by the SDK for all integrations that declare secret fields, not just MCP). It lives in the SDK (`integrations.sdk.secrets`).
- **`INTEGRATION_MAX_TOOLS_PER_SESSION`** is also platform-level (used by the generic tool aggregator for all tool-exposing integrations, not just MCP).
- All other `MCP_*` variables are specific to the MCP Client integration.
- Restart the backend after changing any of these.

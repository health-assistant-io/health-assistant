# Troubleshooting

## "Command 'X' is not in the STDIO allowlist"

The admin must add `X` to `MCP_STDIO_ALLOWED_COMMANDS` (comma-separated) in `.env` and restart the backend.

## "INTEGRATION_SECRET_KEY is not configured"

Generate a Fernet key and set it in `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then restart the backend.

## Connection fails / "Test Connection" returns error

- **STDIO**: check the command is installed on the backend host. `npx` needs Node.js, `uvx` needs uv, etc. Check `logging/backend.log` for the subprocess stderr.
- **HTTP/SSE**: verify the URL, token, and TLS. For self-signed certs, set `ca_bundle_path` (don't disable `verify_ssl` in production).
- Use the **Restart Connection** custom action to force a clean reconnect.

## Tools not appearing in chat

- Use **List Tools** to confirm the server exposes tools.
- Check `enabled_tools` / `disabled_tools` / `include_tags` / `exclude_tags` filters in the config.
- The global cap `INTEGRATION_MAX_TOOLS_PER_SESSION` (default 20) may be reached — raise it or filter more aggressively.
- The chat only loads MCP tools when a patient context is active (per-patient scope, v1).

## "MCP tool 'X' timed out"

Raise `request_timeout` (per-instance, seconds) or `MCP_REQUEST_TIMEOUT` (global default).

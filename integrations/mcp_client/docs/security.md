# Security Model

The MCP Client introduces a meaningful attack surface: it lets users run local subprocesses and call remote tools from chat. This document lists the threats and how they are mitigated.

## Threats & mitigations

| ID | Threat | Mitigation |
|----|--------|------------|
| T1 | Arbitrary code execution via STDIO command | Admin command allowlist (`MCP_STDIO_ALLOWED_COMMANDS`); reject absolute paths and shell metachars; args passed as a list (no shell); `cwd` restricted; per-process concurrency caps. |
| T2 | Secret leakage at rest | SDK-level ``SecretCipher`` (Fernet) encrypts fields declared by ``get_secret_fields()`` before writing to ``user_config``; values are masked as ``***`` in API responses. Key: ``INTEGRATION_SECRET_KEY``. The encryption/masking is done generically by the platform endpoint via the SDK ``prepare_for_storage`` / ``prepare_for_read`` hooks — no MCP-specific code in the endpoint. |
| T3 | Cross-user HTTP header/token contamination | Per-`UserIntegration` httpx client (FastMCP creates one per `Client`); never the SDK shared pool. |
| T4 | Malicious tool descriptions/results (prompt injection) | Description length cap (`MAX_DESCRIPTION_LEN`); result size cap (`MCP_TOOL_RESULT_MAX_BYTES`, default 64 KB) with truncation marker; tool-name sanitization (no `__`); admin global disable via `system_integrations`. |
| T5 | Tokenless proxy exposure | `handle_api_request` refuses tool invocation; only `GET /status` is exposed. All tool calls go through the authenticated `/ai-assistance/stream` endpoint. |
| T6 | Resource exhaustion / DoS | `MCP_MAX_SERVERS_PER_USER` (default 5); `MCP_REQUEST_TIMEOUT` (30 s); idle timeout (default 15 min); `MCP_MAX_TOTAL_STDIO` (20); `MCP_PER_INSTANCE_CONCURRENCY` (4); result size cap. |
| T7 | Transport MITM | `https://` enforced unless `MCP_ALLOW_INSECURE_HTTP=True`; `verify_ssl` default `true`; custom CA bundle supported. |
| T8 | Dropping connections | Auto-reconnect on next call; status surfaced via the **Test Connection** custom action. |

## Encryption key

`INTEGRATION_SECRET_KEY` must be a Fernet key (base64 32 bytes). Generate with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The cipher lives in the SDK (`integrations.sdk.secrets.SecretCipher`) — the MCP config flow declares its secret fields via `get_secret_fields()` and the SDK defaults + platform endpoint handle encryption/masking generically. If the key is missing, saving config fails fast with a 400 — no plaintext secrets are ever stored.

## Tool namespacing

All MCP tools are renamed `mcp__<instance_slug>__<original_name>` before being exposed to the LLM. This:

- Prevents collisions with built-in tools (`get_biomarker_history`, etc.).
- Prevents collisions across MCP instances.
- Lets the chat UI/audit log attribute a tool call to a specific MCP server.

Original tool names containing `__` are rejected (namespace spoofing defense).

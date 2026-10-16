# HTTP / SSE Server Setup

Use the `http` (Streamable HTTP, recommended) or `sse` (legacy) transport to connect to a remote MCP server.

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `url` | yes | Server URL. `https://` required unless `MCP_ALLOW_INSECURE_HTTP=True`. |
| `auth_token` | no | Bearer token sent as `Authorization: Bearer <token>`. **Encrypted at rest.** |
| `headers` | no | Extra HTTP headers. **Encrypted at rest.** |
| `verify_ssl` | no | Verify the server's TLS certificate (default `true`). |
| `ca_bundle_path` | no | Path to a CA bundle for self-signed certs. |

## Example

```json
{
  "instance_name": "Acme MCP",
  "transport": "http",
  "url": "https://mcp.acme.com/mcp",
  "auth_token": "sk-xxx",
  "verify_ssl": true
}
```

## Self-signed servers

Set `verify_ssl: true` and provide `ca_bundle_path: /etc/ssl/acme-ca.pem`. Do **not** disable verification in production.

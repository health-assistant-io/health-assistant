# STDIO Server Setup

Use the `stdio` transport to run an MCP server as a local subprocess.

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `command` | yes | Bare command from the allowlist (e.g. `npx`, `python`, `uvx`). No absolute paths, no shell metachars. |
| `args` | no | List of arguments (e.g. `["-y", "@modelcontextprotocol/server-github"]`). |
| `env` | no | Environment variables for the subprocess. **Encrypted at rest.** Put API keys here (e.g. `GITHUB_PERSONAL_ACCESS_TOKEN`). |
| `cwd` | no | Working directory (absolute path). Must not be a system dir like `/etc`, `/proc`. |
| `keep_alive` | no | Keep the subprocess alive between tool calls (default `true` — recommended). |

## Allowlist

The admin controls which commands users may launch via `MCP_STDIO_ALLOWED_COMMANDS` (comma-separated, default `npx,uvx,python,python3,node`). Users cannot run commands outside the allowlist.

## Example: GitHub MCP

```json
{
  "instance_name": "GitHub",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx" }
}
```

## Security notes

- Args are passed as a list (no shell), so shell injection is not possible.
- Each STDIO subprocess counts toward `MCP_MAX_TOTAL_STDIO` (default 20 across all users on this backend).
- `keep_alive=true` reuses the subprocess across calls for performance; `false` gives full isolation per call.

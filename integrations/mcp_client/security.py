"""MCP-Client-specific security helpers.

Platform-level secret encryption lives in ``integrations.sdk.secrets`` and is
invoked generically by the SDK base / endpoint. This module keeps only the
validators that are genuinely MCP-specific:

- :func:`validate_stdio_command` — command allowlist + shell-metachar/cwd
  defense (threat T1).
- :func:`validate_http_url` — scheme + insecure-http policy (T7).
- :func:`build_ssl_context` — SSLContext from verify/ca-bundle settings (T7).

Threat IDs (T1..T7) refer to the MCP Client integration security plan.
"""

from __future__ import annotations

import logging
import os
import re
import ssl

logger = logging.getLogger(__name__)

_SHELL_META_RE = re.compile(r"[;&|`$<>\n\r\\]")
# Disallow path traversal / absolute paths in the command itself; args are
# passed as a list (no shell) so absolute paths there are fine but cwd is
# constrained separately.
_ABS_PATH_RE = re.compile(r"^(/|[A-Za-z]:[\\/]|[.][.][/\\])")

# Audit 2026-08 C-4: interpreters on the allowlist (python/node/…) must not
# be handed free-form code. These flags accept an inline program string and
# are therefore indistinguishable from "run arbitrary code the user typed".
_INTERPRETER_CODE_FLAGS = {
    "python": {"-c", "--c"},
    "python3": {"-c", "--c"},
    "node": {"-e", "--eval", "-p", "--print", "-pe", "-pd"},
    "deno": {"-e", "--eval", "eval"},
    "ruby": {"-e"},
    "perl": {"-e", "-E"},
    "bash": {"-c"},
    "sh": {"-c"},
    "uvx": set(),
    "npx": set(),
}


def _arg_allows_inline_code(command: str, args: list[str]) -> str | None:
    """Return the offending flag if the launch spec runs inline code."""
    flags = _INTERPRETER_CODE_FLAGS.get(os.path.basename(command.replace("\\", "/")))
    if not flags:
        return None
    for arg in args:
        if arg in flags or arg.split("=", 1)[0] in flags:
            return arg
    return None


def get_allowed_commands() -> list[str]:
    """Parse ``MCP_STDIO_ALLOWED_COMMANDS`` (comma-separated) into a list."""
    from app.core.config import get_settings

    raw = get_settings().MCP_STDIO_ALLOWED_COMMANDS or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def validate_stdio_command(
    command: str,
    args: list[str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Validate a STDIO MCP launch spec against the allowlist + safety rules.

    Returns ``(ok, reason)``. ``reason`` is human-readable and safe to surface
    to the user as a 400 error.
    """
    if not command or not isinstance(command, str):
        return False, "Command is required."

    allowed = get_allowed_commands()
    if not allowed:
        return False, (
            "STDIO MCP servers are disabled on this instance "
            "(MCP_STDIO_ALLOWED_COMMANDS is empty). Use http/sse transport."
        )
    base = os.path.basename(command.replace("\\", "/"))
    if _ABS_PATH_RE.match(command.strip()):
        return False, (
            "Absolute or path-traversal commands are not allowed. Use a bare "
            f"command from the allowlist: {', '.join(allowed)}."
        )
    if base not in allowed:
        return False, (
            f"Command '{base}' is not in the STDIO allowlist. Allowed: "
            f"{', '.join(allowed)}."
        )
    if _SHELL_META_RE.search(command):
        return False, "Command contains forbidden shell metacharacters."

    args = args or []
    for arg in args:
        if not isinstance(arg, str):
            return False, "All args must be strings (no shell expansion is performed)."

    offending = _arg_allows_inline_code(base, args)
    if offending:
        return False, (
            f"Argument '{offending}' would execute inline code and is not "
            "allowed for STDIO MCP servers. Pass a package/script instead."
        )

    if cwd:
        cwd_abs = os.path.abspath(cwd)
        if not os.path.isdir(cwd_abs):
            return False, f"cwd does not exist: {cwd}"
        blocked = ("/etc", "/proc", "/sys", "/dev", "/var/log")
        if cwd_abs in blocked or cwd_abs.startswith(blocked + ("/",)):
            return False, f"cwd is in a restricted system directory: {cwd}"

    return True, ""


def validate_http_url(
    url: str, allow_insecure: bool | None = None
) -> tuple[bool, str]:
    """Validate an HTTP/SSE MCP server URL (scheme + SSRF net-guard)."""
    if not url or not isinstance(url, str):
        return False, "URL is required."
    if not re.match(r"^https?://", url):
        return False, "URL must start with http:// or https://."
    if url.lower().startswith("http://"):
        if allow_insecure is None:
            from app.core.config import get_settings

            allow_insecure = get_settings().MCP_ALLOW_INSECURE_HTTP
        if not allow_insecure:
            return False, (
                "Insecure http:// URLs are disabled. Set MCP_ALLOW_INSECURE_HTTP=True "
                "or use https://."
            )

    # INT-H4 (audit 2026-08): block private/loopback/link-local targets the
    # same way every other outbound SDK call does. Previously
    # https://169.254.169.254 or https://10.0.0.1:8443 validated fine and the
    # backend originated TLS connections (with user-supplied bearer headers)
    # to internal hosts.
    from integrations.sdk.net_guard import SSRFBlockedError, assert_safe_url

    try:
        assert_safe_url(url)
    except SSRFBlockedError as exc:
        return False, f"URL points at a blocked (private/internal) target: {exc}"
    except Exception:
        pass

    return True, ""


def build_ssl_context(verify: bool, ca_bundle_path: str | None) -> ssl.SSLContext:
    """Build an SSLContext for HTTP/SSE transports."""
    if not verify:
        ctx = ssl._create_unverified_context()
        logger.warning("MCP Client: SSL verification disabled by user config.")
        return ctx
    ctx = ssl.create_default_context()
    if ca_bundle_path:
        if not os.path.isfile(ca_bundle_path):
            raise ValueError(f"CA bundle not found: {ca_bundle_path}")
        ctx.load_verify_locations(ca_bundle_path)
    return ctx

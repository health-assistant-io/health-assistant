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
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_SHELL_META_RE = re.compile(r"[;&|`$<>\n\r\\]")
# Disallow path traversal / absolute paths in the command itself; args are
# passed as a list (no shell) so absolute paths there are fine but cwd is
# constrained separately.
_ABS_PATH_RE = re.compile(r"^(/|[A-Za-z]:[\\/]|[.][.][/\\])")


def get_allowed_commands() -> List[str]:
    """Parse ``MCP_STDIO_ALLOWED_COMMANDS`` (comma-separated) into a list."""
    from app.core.config import get_settings

    raw = get_settings().MCP_STDIO_ALLOWED_COMMANDS or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def validate_stdio_command(
    command: str,
    args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate a STDIO MCP launch spec against the allowlist + safety rules.

    Returns ``(ok, reason)``. ``reason`` is human-readable and safe to surface
    to the user as a 400 error.
    """
    if not command or not isinstance(command, str):
        return False, "Command is required."

    allowed = get_allowed_commands()
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

    for arg in args or []:
        if not isinstance(arg, str):
            return False, "All args must be strings (no shell expansion is performed)."

    if cwd:
        cwd_abs = os.path.abspath(cwd)
        if not os.path.isdir(cwd_abs):
            return False, f"cwd does not exist: {cwd}"
        blocked = ("/etc", "/proc", "/sys", "/dev", "/var/log")
        if cwd_abs in blocked or cwd_abs.startswith(blocked + ("/",)):
            return False, f"cwd is in a restricted system directory: {cwd}"

    return True, ""


def validate_http_url(url: str, allow_insecure: Optional[bool] = None) -> Tuple[bool, str]:
    """Validate an HTTP/SSE MCP server URL."""
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
    return True, ""


def build_ssl_context(verify: bool, ca_bundle_path: Optional[str]) -> ssl.SSLContext:
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

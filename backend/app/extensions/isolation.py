"""isolation — the graded T2 containment builders (plan 39 v2).

Vendored byte-identically from ``dev/contracts/isolation.py`` (ADR-0031
data-once). The LAW is ``dev/contracts/sandbox-isolation.md``; the escape
matrix is ``sandbox-isolation.fixtures.json`` (the wall every backend
must pass). Stdlib-only so contract walls can import the module without
host dependencies.

Grades (closed vocabulary): ``kernel`` (the OS stops the child),
``process`` (hardened separate process — env/credentials/cwd/limits, but
NOT kernel-denied network/files; the consent surface must say so),
``none`` (T2 self-disables with the remedy). Policy (D-39): server-mode
hosts that accept imported code require ``kernel``; a host may run T2 at
``process`` only with the posture disclosed and the per-platform opt-in
recorded in the host profile; posture is reported from DETECTION, never
from a constant.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

GRADE_KERNEL = "kernel"
GRADE_PROCESS = "process"
GRADE_NONE = "none"
GRADES = (GRADE_KERNEL, GRADE_PROCESS, GRADE_NONE)

#: The exact environment a process-grade child may see — nothing else is
#: inherited (secrets travel in env on real machines).
PROCESS_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "TZ")

BWRAP = shutil.which("bwrap")


class IsolationUnavailable(RuntimeError):
    """The platform cannot provide the containment grade T2 claims."""


# --------------------------------------------------------------- detection


def available() -> bool:
    """True when the KERNEL primitive is real on this platform."""
    return sys.platform.startswith("linux") and BWRAP is not None


def posture() -> str:
    """The honest grade (sandbox-isolation.md §2/§3.3): ``kernel`` only
    when the primitive is real, else ``none``. Hosts that ship the
    process-grade baseline report ``process`` themselves — this module
    never overstates the kernel boundary."""
    return GRADE_KERNEL if available() else GRADE_NONE


# -------------------------------------------------- kernel grade (bwrap)


def _ro_bind(path: str | Path) -> list[str]:
    candidate = Path(path)
    if not candidate.exists():
        return []
    return ["--ro-bind", str(candidate), str(candidate)]


def wrap_child_command(
    *,
    interpreter: Sequence[str],
    child_path: Path,
    content_dir: Path,
    child_args: Sequence[str],
) -> list[str]:
    """Return the argv that runs the child inside the kernel sandbox.

    ``interpreter`` is the host's spawn argv prefix (usually
    ``[sys.executable]``; a frozen build may carry extra flags).
    ``child_args`` are the arguments after the child script path; the
    content dir is mounted at ``/ext`` and any occurrence of the real
    content-dir path is rewritten to ``/ext`` so the child's protocol
    stays unchanged. Sandbox-isolation.md §4.1 is normative for every
    flag here.
    """
    if not available():
        raise IsolationUnavailable(
            "bubblewrap is unavailable on this platform — T2 self-disables "
            "(install bubblewrap or run on a supported Linux host)"
        )

    binds: list[str] = []
    for system_path in ("/usr", "/lib", "/lib64", "/bin", "/sbin"):
        binds.extend(_ro_bind(system_path))
    # The interpreter (venv or system), the child script's directory (host
    # code, read-only) and any prefix the interpreter needs.
    prefixes = {
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(interpreter[0]).resolve().parent,
        Path(interpreter[0]).resolve().parent.parent,
    }
    for prefix in prefixes:
        if str(prefix) not in ("/", ""):
            binds.extend(_ro_bind(prefix))
    binds.extend(_ro_bind(child_path.parent))

    rewritten = [str(arg).replace(str(content_dir), "/ext") for arg in child_args]
    return [
        str(BWRAP),
        "--unshare-all",
        "--die-with-parent",
        "--clearenv",
        *binds,
        "--ro-bind",
        str(content_dir.resolve()),
        "/ext",
        "--dev",
        "/dev",
        "--tmpfs",
        "/work",
        "--chdir",
        "/work",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/work",
        *interpreter,
        "-I",
        "-B",
        str(child_path),
        *rewritten,
    ]


# ------------------------------------------------- process grade (baseline)


def process_spawn_options(
    *,
    workdir: Path,
    memory_mb: int | None = None,
    cpu_seconds: int | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The portable process-grade hardening (sandbox-isolation.md §4.2):
    scrubbed environment (allowlist only), confined cwd, closed handles,
    fresh session, optional memory/CPU rlimits.

    Platform backends ADD to this (Job Object + restricted token on
    Windows, sandbox-exec profiles on macOS where the kernel grade is
    chosen). This baseline alone is grade ``process`` — it does NOT deny
    network or filesystem at the kernel and the disclosure must say so.
    """
    clean_env = {
        key: (env or {}).get(key, os.environ.get(key, ""))
        for key in PROCESS_ENV_ALLOWLIST
    }
    clean_env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")

    options: dict[str, Any] = {
        "cwd": str(workdir),
        "env": clean_env,
        "close_fds": True,
    }
    if hasattr(os, "setsid"):
        options["start_new_session"] = True
    if (memory_mb is not None or cpu_seconds is not None) and hasattr(os, "setrlimit"):

        def _limits() -> None:  # pragma: no cover - runs in the forked child
            import resource

            if memory_mb is not None:
                cap = int(memory_mb) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            if cpu_seconds is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_seconds), int(cpu_seconds)))

        # Deliberate: rlimits apply in the forked child, before exec.
        options["preexec_fn"] = _limits
    return options


def escape_matrix(contracts_dir: str | Path) -> list[dict[str, Any]]:
    """The fixture cases (sandbox-isolation.fixtures.json) — every
    backend is measured against exactly these probes."""
    import json

    path = Path(contracts_dir) / "sandbox-isolation.fixtures.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document.get("cases") or [])

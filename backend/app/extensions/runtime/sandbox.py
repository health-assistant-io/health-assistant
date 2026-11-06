"""The T2 sandbox spawn seam (plan 7 W4) — the parked engine starts here.

Every sandboxed spawn goes through :func:`spawn_plan`, which composes the
three laws into one typed answer:

1. **Tier is provenance** — the version's envelope derives its tier through
   ``contract.derive_tier``; only derived-T2 content spawns sandboxed.
2. **The host profile is policy data** — the charter's
   ``t2IsolationByPlatform`` names the grade this host may claim per
   platform; a profile that does not declare ``kernel`` answers the typed
   ``unsupported`` with the reason and the remedy — never a silent re-read
   and never a downgraded spawn.
3. **Detection is truth** — ``isolation.posture()`` reports the real grade
   of THIS platform; the declared grade and the detected grade must both be
   ``kernel`` before ``isolation.wrap_child_command`` builds the argv.

The spawn (re-)records the ``extension_isolation_posture`` row — runtime
evidence from DETECTION, never a constant (isolation.py D-39).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions import contract, isolation, lifecycle
from app.extensions.errors import ExtensionError
from app.models.extension_source_model import ExtensionSourceVersion


@dataclass(frozen=True)
class SpawnPlan:
    """The resolved sandbox spawn: the wrapped argv, the detected grade and
    the content dir the child is confined to."""

    argv: list[str]
    grade: str
    content_dir: Path
    derived_tier: str


def declared_isolation(profile: dict[str, Any], platform: str) -> tuple[str, str | None]:
    """The charter profile's declared grade for ``platform`` — policy data.

    Returns ``(grade, reason)``: ``reason`` is ``None`` only when the
    profile declares the ``kernel`` grade; any other declaration (or none
    at all) carries the typed reason the spawn will refuse with.
    """
    declared = (profile.get("t2IsolationByPlatform") or {}).get(platform)
    if declared is None:
        return isolation.GRADE_NONE, (
            f"the host profile declares no t2Isolation grade for {platform!r} — "
            "T2 self-disables (charter hostProfiles data)"
        )
    if declared == isolation.GRADE_KERNEL:
        return declared, None
    return str(declared), (
        f"the host profile declares t2Isolation={declared!r} for {platform!r}; "
        "server-mode T2 requires the kernel grade — T2 self-disables with this reason"
    )


async def spawn_plan(
    db: AsyncSession,
    version: ExtensionSourceVersion,
    *,
    interpreter: Sequence[str],
    child_path: Path,
    content_dir: Path,
    child_args: Sequence[str] = (),
    host_profile: str = "health-assistant",
) -> SpawnPlan:
    """Resolve the sandboxed spawn for an approved T2 version.

    Refuses with the typed ``unsupported`` (+ reason) whenever the profile
    or the platform cannot provide the kernel grade — the hostile case is a
    typed answer, not a fallback. Records the posture row before any spawn.
    """
    derived_tier = contract.derive_tier(version.envelope or {}, bundled=False)
    if derived_tier != "T2":
        raise ExtensionError(
            "unsupported",
            f"tier {derived_tier} does not spawn through the T2 sandbox "
            "(tier is provenance — contract.derive_tier decides)",
        )

    from app.extensions.registry import _profile

    profile = _profile()
    _declared, reason = declared_isolation(profile, sys.platform)
    detected = isolation.posture()
    await lifecycle.upsert_posture_row(
        db,
        version,
        requested_tier="T2",
        derived_tier=derived_tier,
        host_profile=host_profile,
    )

    if reason is not None:
        raise ExtensionError("unsupported", reason)
    if detected != isolation.GRADE_KERNEL:
        raise ExtensionError(
            "unsupported",
            f"detection reports grade {detected!r} on {sys.platform!r} — install "
            "bubblewrap (or run on a supported Linux host); T2 never spawns below "
            "the kernel boundary",
        )

    argv = isolation.wrap_child_command(
        interpreter=list(interpreter),
        child_path=child_path,
        content_dir=content_dir,
        child_args=list(child_args),
    )
    return SpawnPlan(argv=argv, grade=detected, content_dir=content_dir, derived_tier=derived_tier)

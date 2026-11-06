"""The version-bound consent model (plan 7 W3 — ADR-0040 consent law).

Consent binds to a manifest DIGEST (via ``extension_source_versions``): an
upgraded extension re-prompts for what is new, while capabilities that did
not change carry their grants forward as new rows (the browser-permission
model). The 8C change-summary surface renders the delta between the RUNNING
version's declared capabilities and the incoming version's — new
capabilities demand a decision, unchanged ones stay granted, and removed
ones are reported without re-prompting.

x-secret slots seal PER VERSION through the existing credential seam: the
sealed payload carries the version digest it was sealed for, so an upgrade
reports the slots stale instead of silently reusing secret material
resolved for content that is no longer running.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions import lifecycle
from app.extensions.errors import ExtensionError
from app.models.enums import (
    ExtensionConsentDecision,
    ExtensionVersionStatus,
)
from app.models.extension_source_model import ExtensionConsent, ExtensionSourceVersion

DECIDED_BY_CARRY_OVER = "carry-over"


def declared_capabilities(version: ExtensionSourceVersion) -> set[str]:
    return set((version.envelope or {}).get("capabilities") or [])


def disclosure_for(version: ExtensionSourceVersion) -> dict[str, str]:
    """The tier's disclosure VERBATIM from the vendored charter, with its
    key — the surface renders this text, never a paraphrase."""
    text, key = lifecycle.disclosure_for_tier(_imported_tier(version))
    return {"disclosureKey": key, "disclosure": text}


def _imported_tier(version: ExtensionSourceVersion) -> str:
    from app.extensions import contract

    return contract.derive_tier(version.envelope or {}, bundled=False)


async def consent_delta(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    incoming: ExtensionSourceVersion,
    running: ExtensionSourceVersion | None,
) -> dict[str, Any]:
    """The 8C change-summary: what changed between the running digest and
    the incoming one, which capabilities need a decision, and which grants
    survive unchanged."""
    incoming_caps = declared_capabilities(incoming)
    running_caps = declared_capabilities(running) if running else set()
    grants = await granted_capabilities(db, user_id=user_id, version=running) if running else set()

    added = sorted(incoming_caps - running_caps)
    removed = sorted(running_caps - incoming_caps)
    unchanged = sorted(incoming_caps & running_caps)
    return {
        "versionId": str(incoming.id),
        "incomingDigest": incoming.digest,
        "runningDigest": running.digest if running else None,
        "version": {
            "from": (running.envelope or {}).get("version") if running else None,
            "to": (incoming.envelope or {}).get("version"),
        },
        "capabilitiesAdded": added,
        "capabilitiesRemoved": removed,
        "capabilitiesUnchanged": unchanged,
        "keptGrants": sorted(grants & incoming_caps),
        "requiresReConsent": bool(added),
        "status": incoming.status.value if incoming.status else None,
        **disclosure_for(incoming),
    }


async def granted_capabilities(
    db: AsyncSession, *, user_id: UUID, version: ExtensionSourceVersion
) -> set[str]:
    rows = (
        (
            await db.execute(
                select(ExtensionConsent).where(
                    ExtensionConsent.user_id == user_id,
                    ExtensionConsent.version_id == version.id,
                    ExtensionConsent.decision == ExtensionConsentDecision.GRANTED,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.capability for row in rows}


async def record_decisions(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    version: ExtensionSourceVersion,
    decisions: dict[str, str],
    decided_by: UUID | None,
    echo: dict[str, Any] | None = None,
) -> list[ExtensionConsent]:
    """Write one consent row per (user, version, capability).

    The host-derived consent echo is verified FIRST (a forged version is
    refused typed); every decided capability must be declared by the
    version's envelope — deciding an undeclared capability is the typed
    ``invalid``. Decisions never carry over silently: they bind to THIS
    digest.
    """
    if echo is not None:
        lifecycle.verify_consent_echo(echo, version.envelope or {}, version.digest)
    declared = declared_capabilities(version)
    unknown = sorted(set(decisions) - declared)
    if unknown:
        raise ExtensionError(
            "invalid",
            f"capabilities {unknown} are not declared by version "
            f"{(version.envelope or {}).get('version')!r} — refusing to record",
        )
    rows: list[ExtensionConsent] = []
    now = datetime.now(UTC)
    for capability, raw in sorted(decisions.items()):
        try:
            decision = ExtensionConsentDecision(raw)
        except ValueError:
            raise ExtensionError(
                "invalid",
                f"consent decision must be one of "
                f"{[member.value for member in ExtensionConsentDecision]} — got {raw!r}",
            ) from None
        stmt = select(ExtensionConsent).where(
            ExtensionConsent.user_id == user_id,
            ExtensionConsent.version_id == version.id,
            ExtensionConsent.capability == capability,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ExtensionConsent(
                tenant_id=tenant_id,
                user_id=user_id,
                version_id=version.id,
                capability=capability,
                decision=decision,
                scope_json={},
                decided_at=now,
                decided_by=decided_by,
            )
            db.add(row)
        else:
            row.decision = decision
            row.decided_at = now
            row.decided_by = decided_by
        rows.append(row)
    await db.flush()
    return rows


async def carry_over_grants(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    running: ExtensionSourceVersion,
    incoming: ExtensionSourceVersion,
) -> list[ExtensionConsent]:
    """Copy the running version's GRANTED rows onto the incoming digest for
    capabilities that did not change — the unchanged set keeps its grants;
    everything new stays ungranted until decided."""
    grants = await granted_capabilities(db, user_id=user_id, version=running)
    surviving = grants & declared_capabilities(incoming) & declared_capabilities(running)
    rows: list[ExtensionConsent] = []
    now = datetime.now(UTC)
    for capability in sorted(surviving):
        stmt = select(ExtensionConsent).where(
            ExtensionConsent.user_id == user_id,
            ExtensionConsent.version_id == incoming.id,
            ExtensionConsent.capability == capability,
        )
        if (await db.execute(stmt)).scalar_one_or_none() is not None:
            continue
        row = ExtensionConsent(
            tenant_id=tenant_id,
            user_id=user_id,
            version_id=incoming.id,
            capability=capability,
            decision=ExtensionConsentDecision.GRANTED,
            scope_json={},
            decided_at=now,
            decided_by=None,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def pending_consents(
    db: AsyncSession, *, user_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Approved versions with no consent rows yet for this user — the
    consent inbox the 8C surface renders."""
    versions = (
        (
            await db.execute(
                select(ExtensionSourceVersion)
                .where(ExtensionSourceVersion.status == ExtensionVersionStatus.APPROVED)
                .order_by(ExtensionSourceVersion.first_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    pending: list[dict[str, Any]] = []
    for version in versions:
        decided = (
            (
                await db.execute(
                    select(ExtensionConsent).where(
                        ExtensionConsent.user_id == user_id,
                        ExtensionConsent.version_id == version.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if decided is not None:
            continue
        pending.append(
            {
                "versionId": str(version.id),
                "digest": version.digest,
                "extensionId": (version.envelope or {}).get("id"),
                "version": (version.envelope or {}).get("version"),
                "capabilities": sorted(declared_capabilities(version)),
                **disclosure_for(version),
            }
        )
    return pending


def declared_secret_slots(version: ExtensionSourceVersion) -> list[dict[str, Any]]:
    """The version's x-secret declarations: manifest ``secrets[]`` slots and
    ``configSchema`` properties marked ``x-secret: true`` — slot NAMES only,
    never values (the x-secret law)."""
    envelope = version.envelope or {}
    slots: dict[str, dict[str, Any]] = {}
    for entry in envelope.get("secrets") or []:
        if isinstance(entry, dict) and entry.get("id"):
            slots[str(entry["id"])] = {
                "slot": str(entry["id"]),
                "purpose": entry.get("purpose"),
                "domains": [str(domain) for domain in entry.get("domains") or []],
            }
    properties = ((envelope.get("configSchema") or {}).get("properties")) or {}
    for name, field in properties.items():
        if isinstance(field, dict) and field.get("x-secret") is True:
            slots.setdefault(str(name), {"slot": str(name), "purpose": None, "domains": []})
    return sorted(slots.values(), key=lambda slot: slot["slot"])


VERSION_DIGEST_KEY = "_version_digest"


def stamp_version_secrets(payload: dict[str, Any], version: ExtensionSourceVersion) -> dict:
    """The per-version seal stamp: the credential payload records the digest
    it was sealed FOR (the config-flow/consent flow seals through the
    existing credential seam with this payload)."""
    return {**payload, VERSION_DIGEST_KEY: version.digest}


def version_secret_state(
    stored_payload: dict[str, Any] | None, version: ExtensionSourceVersion
) -> str:
    """``missing`` (no sealed payload), ``stale`` (sealed for a different
    digest — an upgrade must re-bind), or ``sealed`` (bound to THIS
    digest)."""
    if not stored_payload:
        return "missing"
    if stored_payload.get(VERSION_DIGEST_KEY) != version.digest:
        return "stale"
    return "sealed"

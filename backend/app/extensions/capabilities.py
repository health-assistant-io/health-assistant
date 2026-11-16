"""Host-enforced extension capability grants (ADR 0030 / ADR 0040 / 0054 D8).

The manifest is the *declaration of record*: what an extension may do is the
closed ``capabilities`` taxonomy plus the connector-kind ``itemSchema`` data
classes it declares — never what its code happens to expose. This module turns
that declaration into one typed :class:`CapabilityGrant` per extension and
tier, and every host seam asks the grant instead of probing the extension.

Tier rules (``contract.derive_tier`` is the one derivation):

- **T0** (imported, no entry) — declarative data only. The grant carries no
  capabilities and no permissions: nothing executes, nothing reaches the
  network or the database.
- **T1** (bundled) — reviewed host-integrated code. The grant carries exactly
  the manifest's declared capabilities and data classes; the engine calls only
  what the grant allows (no ambient capability by method presence).
- **T2** (imported + entry) — sandboxed capability-RPC. The grant is the
  declared set **after consent**: a capability the user never granted is not
  in the set, and an undeclared capability can never be called. T2 code
  receives no host objects and therefore no database access; only the
  host-mediated ``cap.net.fetch`` / ``cap.state.*`` / ``cap.items.emit`` /
  ``cap.secrets.need`` seams exist.
- **T3** (imported + entry + mcp) — a separate MCP process outside the app's
  perimeter. Its tools are mapped to the principal's scopes by risk class
  (:meth:`CapabilityGrant.authorize_tool`): ``read-only`` needs the declared
  read permissions, ``state-changing`` the write permissions, and
  ``destructive`` a delete permission the declaration actually carries. The
  host never hands the process a database handle.

Consent revocation is an input, not a follow-up: an imported grant is built
from the granted consent set, so flipping a decision removes the capability
from the very next grant.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.authz.permissions import Permission
from app.extensions import contract
from app.extensions.errors import ExtensionError

_CHARTER_PATH = Path(__file__).resolve().parents[3] / "contracts" / "extension-charter.json"
_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"


def bundled_manifest(domain: str) -> dict[str, Any] | None:
    """The bundled manifest of ``domain``, read from disk (registration-independent).

    Capability derivation must not depend on whether the registry has been
    initialized in this process (a worker restart, a test importing a service
    directly): the declaration of record is the manifest file, so read it.
    """
    path = _BUILTIN_DIR / domain / "manifest.json"
    if not path.exists():
        path = _BUILTIN_DIR / domain.replace("-", "_") / "manifest.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def grant_for_domain(domain: str | None) -> CapabilityGrant:
    """The host-enforced grant for a bundled extension domain (fail closed)."""
    if not domain:
        return grant_for_manifest(None, bundled=False, tier="T0")
    manifest = bundled_manifest(domain)
    if manifest is None:
        from app.extensions.registry import integration_registry

        manifest = integration_registry.get_manifest(domain)
    return grant_for_manifest(manifest, bundled=True)


READ_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.PATIENT_READ,
        Permission.CLINICAL_READ,
        Permission.CATALOG_READ,
        Permission.DOCUMENT_READ,
        Permission.INTEGRATION_CONSENT,
    }
)
WRITE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.PATIENT_WRITE,
        Permission.CLINICAL_WRITE,
        Permission.CATALOG_CREATE,
        Permission.CATALOG_MODERATE,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_PRESIGN,
    }
)
DELETE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.PATIENT_DELETE,
        Permission.CLINICAL_DELETE,
        Permission.DOCUMENT_DELETE,
    }
)

#: Charter capability → the app permissions the host may exercise on its
#: behalf. ``net``/``secrets``/UI capabilities are host-mediated surfaces, not
#: database permissions (they never appear here).
CAPABILITY_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "sync": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "push": frozenset(),
    "notify": frozenset(),
    "inbound-webhook": frozenset({Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}),
    "inbound-api": frozenset(
        {
            # An inbound-API integration is a remote client of the app's own
            # surfaces (the mobile bridge serves chat/assistant turns), so it
            # carries ``ai:use`` with the read/write families. The host still
            # re-intersects with the owner's live role, and a credential
            # without ``ai:use`` is refused at the AI seams.
            Permission.PATIENT_READ,
            Permission.CLINICAL_READ,
            Permission.CLINICAL_WRITE,
            Permission.DOCUMENT_READ,
            Permission.DOCUMENT_WRITE,
            Permission.AI_USE,
        }
    ),
    "chat-tools": frozenset({Permission.AI_USE}),
    "report": frozenset(),
    "propose": frozenset(),
    "state": frozenset(),
    "secrets": frozenset(),
    "net": frozenset(),
}

#: Connector-kind data class (``itemSchema`` property) → permissions required
#: to persist that item. These are the write paths the sync engine owns.
DATA_CLASS_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "observation": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "clinical_event": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "examination": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "document": frozenset(
        {
            Permission.PATIENT_READ,
            Permission.DOCUMENT_READ,
            Permission.DOCUMENT_WRITE,
            Permission.DOCUMENT_PRESIGN,
        }
    ),
    "lab_report": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "medication": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "allergy": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "immunization": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
    "catalog_proposal": frozenset(
        {Permission.CATALOG_READ, Permission.CATALOG_CREATE, Permission.CATALOG_MODERATE}
    ),
    "hitl_proposal": frozenset(
        {Permission.PATIENT_READ, Permission.CLINICAL_READ, Permission.CLINICAL_WRITE}
    ),
}

#: Risk class → the permission family a T3 tool call must hold.
RISK_CLASS_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "read-only": READ_PERMISSIONS,
    "state-changing": WRITE_PERMISSIONS,
    "destructive": DELETE_PERMISSIONS,
}


@lru_cache(maxsize=1)
def _charter() -> dict[str, Any]:
    return json.loads(_CHARTER_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _capability_tiers() -> dict[str, frozenset[int]]:
    return {
        str(entry.get("id")): frozenset(int(tier) for tier in entry.get("tiers") or [])
        for entry in _charter().get("capabilities") or []
        if isinstance(entry, dict) and entry.get("id")
    }


def tier_number(tier: str) -> int:
    """``"T2"`` → ``2`` (the charter vocabulary is ``T0``..``T3``)."""
    return int(str(tier).lstrip("Tt") or 0)


def capability_allowed_at_tier(capability: str, tier: str) -> bool:
    """Whether the charter hosts ``capability`` at ``tier``."""
    return tier_number(tier) in _capability_tiers().get(capability, frozenset())


@dataclass(frozen=True)
class CapabilityGrant:
    """The exact capability set one extension may exercise on this host."""

    extension_id: str
    tier: str
    capabilities: frozenset[str]
    data_classes: frozenset[str]
    permissions: frozenset[Permission]
    database_access: bool = False
    declared: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_data_only(self) -> bool:
        """T0/T1 declarative content — no executed code at all."""
        return self.tier in ("T0", "T1") and not self.capabilities

    def allows(self, capability: str) -> bool:
        """Whether the host may exercise ``capability`` for this extension."""
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        """Refuse an undeclared/ungranted capability with the typed answer."""
        if not self.allows(capability):
            raise ExtensionError(
                "unsupported",
                f"capability {capability!r} is not granted to {self.extension_id!r} "
                f"at {self.tier} — the manifest does not declare it (or consent "
                "was not granted); the call is refused, never dispatched",
            )

    def allows_data_class(self, data_class: str) -> bool:
        """Whether the extension declared the connector-kind data class."""
        return data_class in self.data_classes

    def require_permission(self, permission: Permission) -> None:
        """Refuse a host-side action outside the grant's permission set."""
        if permission not in self.permissions:
            raise ExtensionError(
                "unsupported",
                f"{self.extension_id!r} does not declare the capability that "
                f"carries {permission.value!r} — refusing the host-side write",
            )

    def tool_permissions(self, risk_class: str) -> frozenset[Permission]:
        """The permissions a T3/MCP tool call must hold for its risk class."""
        family = RISK_CLASS_PERMISSIONS.get(risk_class)
        if family is None:
            raise ExtensionError("invalid", f"unknown tool risk class {risk_class!r}")
        return frozenset(self.permissions & family)

    def authorize_tool(self, risk_class: str) -> frozenset[Permission]:
        """Map a tool's risk class to scopes; refuse when the grant is empty."""
        scopes = self.tool_permissions(risk_class)
        if not scopes:
            raise ExtensionError(
                "unsupported",
                f"{self.extension_id!r} has no {risk_class!r} scopes for a tool call "
                "— the declaration does not carry them",
            )
        return scopes


#: Risk classes ordered by blast radius (the manifest vocabulary).
RISK_CLASS_WEIGHT: dict[str, int] = {
    "read-only": 0,
    "state-changing": 1,
    "destructive": 2,
}


def manifest_risk_class(manifest: Mapping[str, Any] | None) -> str:
    """The heaviest risk class a manifest's tool contributions declare.

    A T3/MCP proxy publishes one generic contribution for every external tool,
    so the host authorizes the whole tool list at the extension's heaviest
    declared class. ``state-changing`` is the fail-safe default when a
    declaration is missing (the schema rejects that at registration).
    """
    declared = [
        str(entry.get("riskClass"))
        for entry in (manifest or {}).get("tools") or []
        if isinstance(entry, Mapping) and entry.get("riskClass") in RISK_CLASS_WEIGHT
    ]
    if not declared:
        return "state-changing"
    return max(declared, key=RISK_CLASS_WEIGHT.__getitem__)


def grant_for_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    bundled: bool,
    granted_capabilities: set[str] | None = None,
    tier: str | None = None,
) -> CapabilityGrant:
    """Build the host-enforced grant for one manifest.

    ``granted_capabilities`` is the consent intersection for imported content
    (``None`` means "bundled/reviewed ⇒ every declared capability"). The
    returned set is always the intersection of the declaration with the
    charter's tier legality and, when supplied, the consent set — a grant can
    never widen beyond any of the three.
    """
    document = dict(manifest or {})
    derived = tier or contract.derive_tier(document, bundled=bundled)
    declared = frozenset(str(value) for value in (document.get("capabilities") or []))
    item_schema = (document.get("itemSchema") or {}).get("properties") or {}
    data_classes = frozenset(str(value) for value in item_schema)

    if derived == "T0":
        effective: frozenset[str] = frozenset()
    else:
        legal = frozenset(
            capability for capability in declared if capability_allowed_at_tier(capability, derived)
        )
        if granted_capabilities is not None:
            effective = legal & frozenset(granted_capabilities)
        else:
            effective = legal

    permissions: set[Permission] = set()
    for capability in effective:
        permissions.update(CAPABILITY_PERMISSIONS.get(capability, frozenset()))
    for data_class in data_classes:
        permissions.update(DATA_CLASS_PERMISSIONS.get(data_class, frozenset()))

    return CapabilityGrant(
        extension_id=str(document.get("id") or ""),
        tier=derived,
        capabilities=effective,
        data_classes=data_classes,
        permissions=frozenset(permissions),
        database_access=False,
        declared=declared,
    )


__all__ = [
    "CAPABILITY_PERMISSIONS",
    "DATA_CLASS_PERMISSIONS",
    "DELETE_PERMISSIONS",
    "READ_PERMISSIONS",
    "RISK_CLASS_PERMISSIONS",
    "RISK_CLASS_WEIGHT",
    "WRITE_PERMISSIONS",
    "CapabilityGrant",
    "bundled_manifest",
    "capability_allowed_at_tier",
    "grant_for_domain",
    "grant_for_manifest",
    "manifest_risk_class",
    "tier_number",
]

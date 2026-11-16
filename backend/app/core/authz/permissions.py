"""The permission catalog — the closed vocabulary of authorization decisions.

Every permission is ``resource:action`` (a :class:`enum.StrEnum` member, so it
compares equal to its string form on the wire and in tokens). The catalog is
code, not DB rows (ADR 0054 D1): adding a member is a reviewed code change
that the generated matrix and the role-default tests pick up automatically.

Role defaults live in :mod:`app.core.authz.roles`; route declarations live in
:mod:`app.core.authz.policy`.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator


class Permission(enum.StrEnum):
    """Every authorizable action in the platform (closed set).

    Naming: ``RESOURCE_ACTION`` ↔ value ``resource:action``. Destructive or
    bulk actions are always their own member so a grant can never silently
    widen a read into an export or a delete.
    """

    TENANT_READ = "tenant:read"
    TENANT_MANAGE = "tenant:manage"
    TENANT_CREATE = "tenant:create"
    TENANT_SWITCH = "tenant:switch"

    USER_READ = "user:read"
    USER_INVITE = "user:invite"
    USER_ROLE_ASSIGN = "user:role_assign"
    USER_DEACTIVATE = "user:deactivate"

    PATIENT_READ = "patient:read"
    PATIENT_WRITE = "patient:write"
    PATIENT_DELETE = "patient:delete"
    PATIENT_GRANT = "patient:grant"

    CLINICAL_READ = "clinical:read"
    CLINICAL_WRITE = "clinical:write"
    CLINICAL_DELETE = "clinical:delete"

    CATALOG_READ = "catalog:read"
    CATALOG_CREATE = "catalog:create"
    CATALOG_MODERATE = "catalog:moderate"
    CATALOG_PROMOTE = "catalog:promote"

    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    DOCUMENT_DELETE = "document:delete"
    DOCUMENT_PRESIGN = "document:presign"

    EXPORT_PATIENT = "export:patient"
    EXPORT_GROUP = "export:group"
    EXPORT_SYSTEM = "export:system"

    IMPORT_FHIR = "import:fhir"

    AI_USE = "ai:use"
    AI_CONFIG_READ = "ai:config_read"
    AI_CONFIG_WRITE = "ai:config_write"

    INTEGRATION_CONNECT = "integration:connect"
    INTEGRATION_MANAGE = "integration:manage"
    INTEGRATION_CONSENT = "integration:consent"

    SOURCE_MANAGE = "source:manage"

    SEED_SOURCE_READ = "seed_source:read"
    SEED_SOURCE_MANAGE = "seed_source:manage"
    SEED_SOURCE_APPROVE = "seed_source:approve"

    AUDIT_READ = "audit:read"

    LIFECYCLE_REQUEST = "lifecycle:request"
    LIFECYCLE_EXECUTE = "lifecycle:execute"

    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"

    BREAK_GLASS_REQUEST = "break_glass:request"
    BREAK_GLASS_APPROVE = "break_glass:approve"

    def resource(self) -> str:
        """The resource family (the part before the colon)."""
        return self.value.split(":", 1)[0]

    def action(self) -> str:
        """The action (the part after the colon)."""
        return self.value.split(":", 1)[1]


PERMISSION_GROUPS: dict[str, tuple[Permission, ...]] = {
    "tenant": (
        Permission.TENANT_READ,
        Permission.TENANT_MANAGE,
        Permission.TENANT_CREATE,
        Permission.TENANT_SWITCH,
    ),
    "user": (
        Permission.USER_READ,
        Permission.USER_INVITE,
        Permission.USER_ROLE_ASSIGN,
        Permission.USER_DEACTIVATE,
    ),
    "patient": (
        Permission.PATIENT_READ,
        Permission.PATIENT_WRITE,
        Permission.PATIENT_DELETE,
        Permission.PATIENT_GRANT,
    ),
    "clinical": (
        Permission.CLINICAL_READ,
        Permission.CLINICAL_WRITE,
        Permission.CLINICAL_DELETE,
    ),
    "catalog": (
        Permission.CATALOG_READ,
        Permission.CATALOG_CREATE,
        Permission.CATALOG_MODERATE,
        Permission.CATALOG_PROMOTE,
    ),
    "document": (
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_DELETE,
        Permission.DOCUMENT_PRESIGN,
    ),
    "export/import": (
        Permission.EXPORT_PATIENT,
        Permission.EXPORT_GROUP,
        Permission.EXPORT_SYSTEM,
        Permission.IMPORT_FHIR,
    ),
    "ai": (
        Permission.AI_USE,
        Permission.AI_CONFIG_READ,
        Permission.AI_CONFIG_WRITE,
    ),
    "integration": (
        Permission.INTEGRATION_CONNECT,
        Permission.INTEGRATION_MANAGE,
        Permission.INTEGRATION_CONSENT,
    ),
    "source": (Permission.SOURCE_MANAGE,),
    "seed_source": (
        Permission.SEED_SOURCE_READ,
        Permission.SEED_SOURCE_MANAGE,
        Permission.SEED_SOURCE_APPROVE,
    ),
    "audit": (Permission.AUDIT_READ,),
    "lifecycle": (
        Permission.LIFECYCLE_REQUEST,
        Permission.LIFECYCLE_EXECUTE,
    ),
    "settings": (
        Permission.SETTINGS_READ,
        Permission.SETTINGS_WRITE,
    ),
    "break_glass": (
        Permission.BREAK_GLASS_REQUEST,
        Permission.BREAK_GLASS_APPROVE,
    ),
}


def iter_permissions_by_resource() -> Iterator[tuple[str, tuple[Permission, ...]]]:
    """Yield ``(resource, permissions)`` groups in catalog order."""
    yield from PERMISSION_GROUPS.items()

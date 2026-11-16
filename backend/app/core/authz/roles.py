"""Role → permission defaults and the per-role scope rules.

The four system roles are fixed (``app.models.enums.Role``); care-team grants
and custom roles are later phases of ADR 0054. The mapping below is the
Phase-0 baseline translated from the reviewed draft matrix
(``plans/authorization-and-access-model-2026-11-09.md`` §1): it records
*target* defaults — route enforcement still runs today's checks until the
route-gate migration lands, and every deviation observed then is folded back
into this table (one place, tested).

Scope rules narrow how a role holds a permission (who it may act on); they
never widen it. ``ROLE_SCOPE_RULES`` records the *static* baseline and must
carry an explicit entry for every permission whose scope differs from the
role default — ``scope_rule`` applies the default only to held permissions
without an entry and returns ``None`` for permissions the role does not
hold. The runtime knobs (``manager_scope``, tenant settings) are Phase-2
grant-filter inputs consumed at request time; they are deliberately not
encoded here (ADR 0054 — the static rule is the draft-matrix default:
MANAGER patient read/write/export are tenant-wide by default and narrow to
assigned-only when a tenant sets ``manager_scope=assigned``).

Phase-0 tightening of the draft matrix (recorded in ADR 0054): destructive
deletes (``patient:delete``, ``clinical:delete``) are ADMIN/SYSTEM_ADMIN only
in the baseline — the draft granted them tenant-wide to MANAGER — and
self-erasure goes through the audited ``lifecycle:request`` path instead of
direct row deletion for MANAGER/USER.

Phase-12A (break-glass elevations): ADMIN additionally holds
``break_glass:approve`` — a tenant elevation needs a second ADMIN (D1's
two-person rule), and the approval *is* the elevation's decision point.
MANAGER keeps ``break_glass:request`` only: requesters can never approve
their own (or each other's) windows.
"""

from __future__ import annotations

from app.core.authz.permissions import Permission
from app.core.authz.scopes import AccessScope, cap_scope
from app.models.enums import Role

type _RoleScopeRule = dict[Permission, AccessScope]


def _p(*permissions: Permission) -> frozenset[Permission]:
    return frozenset(permissions)


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SYSTEM_ADMIN: _p(*Permission),
    Role.ADMIN: _p(
        Permission.TENANT_READ,
        Permission.TENANT_MANAGE,
        Permission.USER_READ,
        Permission.USER_INVITE,
        Permission.USER_ROLE_ASSIGN,
        Permission.USER_DEACTIVATE,
        Permission.PATIENT_READ,
        Permission.PATIENT_WRITE,
        Permission.PATIENT_DELETE,
        Permission.PATIENT_GRANT,
        Permission.CLINICAL_READ,
        Permission.CLINICAL_WRITE,
        Permission.CLINICAL_DELETE,
        Permission.CATALOG_READ,
        Permission.CATALOG_CREATE,
        Permission.CATALOG_MODERATE,
        Permission.CATALOG_PROMOTE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_DELETE,
        Permission.DOCUMENT_PRESIGN,
        Permission.EXPORT_PATIENT,
        Permission.EXPORT_GROUP,
        Permission.EXPORT_SYSTEM,
        Permission.IMPORT_FHIR,
        Permission.AI_USE,
        Permission.AI_CONFIG_READ,
        Permission.AI_CONFIG_WRITE,
        Permission.INTEGRATION_CONNECT,
        Permission.INTEGRATION_MANAGE,
        Permission.INTEGRATION_CONSENT,
        Permission.SEED_SOURCE_READ,
        Permission.SEED_SOURCE_MANAGE,
        Permission.SEED_SOURCE_APPROVE,
        Permission.AUDIT_READ,
        Permission.LIFECYCLE_REQUEST,
        Permission.LIFECYCLE_EXECUTE,
        Permission.SETTINGS_READ,
        Permission.SETTINGS_WRITE,
        Permission.BREAK_GLASS_REQUEST,
        Permission.BREAK_GLASS_APPROVE,
    ),
    Role.MANAGER: _p(
        Permission.TENANT_READ,
        Permission.USER_INVITE,
        Permission.PATIENT_READ,
        Permission.PATIENT_WRITE,
        Permission.PATIENT_GRANT,
        Permission.CLINICAL_READ,
        Permission.CLINICAL_WRITE,
        Permission.CATALOG_READ,
        Permission.CATALOG_CREATE,
        Permission.CATALOG_MODERATE,
        Permission.CATALOG_PROMOTE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_PRESIGN,
        Permission.EXPORT_PATIENT,
        Permission.EXPORT_GROUP,
        Permission.IMPORT_FHIR,
        Permission.AI_USE,
        Permission.AI_CONFIG_READ,
        Permission.INTEGRATION_CONNECT,
        Permission.INTEGRATION_CONSENT,
        Permission.SETTINGS_READ,
        Permission.LIFECYCLE_REQUEST,
        Permission.BREAK_GLASS_REQUEST,
    ),
    Role.USER: _p(
        Permission.PATIENT_READ,
        Permission.PATIENT_WRITE,
        Permission.PATIENT_GRANT,
        Permission.CLINICAL_READ,
        Permission.CLINICAL_WRITE,
        Permission.CATALOG_READ,
        Permission.CATALOG_CREATE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_PRESIGN,
        Permission.EXPORT_PATIENT,
        Permission.AI_USE,
        Permission.AI_CONFIG_READ,
        Permission.AI_CONFIG_WRITE,
        Permission.INTEGRATION_CONNECT,
        Permission.INTEGRATION_CONSENT,
        Permission.LIFECYCLE_REQUEST,
    ),
}


def _default_scope(role: Role) -> AccessScope:
    return {
        Role.SYSTEM_ADMIN: AccessScope.SYSTEM,
        Role.ADMIN: AccessScope.TENANT,
        Role.MANAGER: AccessScope.TENANT,
        Role.USER: AccessScope.OWN,
    }[role]


ROLE_SCOPE_RULES: dict[Role, _RoleScopeRule] = {
    Role.SYSTEM_ADMIN: {},
    Role.ADMIN: {
        Permission.EXPORT_SYSTEM: AccessScope.TENANT,
        Permission.SEED_SOURCE_MANAGE: AccessScope.TENANT,
        Permission.SEED_SOURCE_APPROVE: AccessScope.TENANT,
        Permission.AI_CONFIG_WRITE: AccessScope.TENANT,
        Permission.INTEGRATION_MANAGE: AccessScope.TENANT,
    },
    Role.MANAGER: {
        Permission.PATIENT_READ: AccessScope.TENANT,
        Permission.PATIENT_WRITE: AccessScope.TENANT,
        Permission.EXPORT_PATIENT: AccessScope.TENANT,
        Permission.INTEGRATION_CONNECT: AccessScope.OWN,
        Permission.INTEGRATION_CONSENT: AccessScope.OWN,
        Permission.USER_INVITE: AccessScope.TENANT,
    },
    Role.USER: {
        Permission.AI_CONFIG_WRITE: AccessScope.OWN,
        Permission.EXPORT_PATIENT: AccessScope.OWN,
        Permission.INTEGRATION_CONNECT: AccessScope.OWN,
        Permission.INTEGRATION_CONSENT: AccessScope.OWN,
    },
}


def permissions_for(role: Role | str) -> frozenset[Permission]:
    """The permissions a role holds under the Phase-0 defaults."""
    return ROLE_PERMISSIONS[Role(role)]


def role_has(role: Role | str, permission: Permission) -> bool:
    """Whether a role holds a permission (scope rules applied by callers)."""
    return permission in permissions_for(role)


def roles_with(permission: Permission) -> tuple[Role, ...]:
    """Every role that holds the permission, in declaration order."""
    return tuple(role for role in Role if permission in ROLE_PERMISSIONS[role])


#: The widest reach a scoped machine credential may carry. A personal access
#: token or integration principal is a bearer secret, never the system actor:
#: even a SYSTEM_ADMIN owner's credential is capped at tenant reach (ADR 0054
#: Phase 6/13).
SCOPED_PRINCIPAL_SCOPE_CAP = AccessScope.TENANT


def _permission_or_none(value: object) -> Permission | None:
    try:
        return Permission(value)  # type: ignore[arg-type]
    except ValueError:
        return None


def is_scoped_principal(principal) -> bool:
    """Whether ``principal`` is a scoped machine credential (PAT/principal)."""
    return isinstance(getattr(principal, "pat_id", None), str) or isinstance(
        getattr(principal, "integration_principal_id", None), str
    )


def is_unscoped_system_admin(principal) -> bool:
    """Whether ``principal`` is a human SYSTEM_ADMIN session (not a credential).

    A PAT or integration principal carries its owner's role *name*, so a
    ``role == SYSTEM_ADMIN`` branch alone would hand a scoped credential the
    operator role's unscoped reach (cross-tenant audit reads, system-wide
    broadcasts, the system elevation ledger). Scoped credentials are capped at
    tenant scope (ADR 0054 D8): every role-trust branch must ask this helper
    instead of comparing the role string.
    """
    raw_role = getattr(principal, "role", None)
    if not isinstance(raw_role, (str, Role)):
        return False
    try:
        role = Role(str(raw_role))
    except ValueError:
        return False
    return role is Role.SYSTEM_ADMIN and not is_scoped_principal(principal)


def _scoped_permissions(principal) -> list[str]:
    if isinstance(getattr(principal, "pat_id", None), str):
        return list(principal.pat_permissions or [])
    return list(getattr(principal, "integration_permissions", None) or [])


def principal_permissions(principal) -> frozenset[Permission]:
    """The permission set a principal holds *for this request*.

    A scoped credential carries its own permission list — a PAT's
    ``pat_permissions`` or an integration principal's
    ``integration_permissions`` — already intersected with the owner's
    **live** role (and, for an integration principal, the manifest
    declaration) by the resolver on every use. A session principal falls
    back to the role defaults.
    """
    if is_scoped_principal(principal):
        resolved = {
            permission
            for permission in (
                _permission_or_none(value) for value in _scoped_permissions(principal)
            )
            if permission is not None
        }
        return frozenset(resolved)
    return permissions_for(principal.role)


def principal_has(principal, permission: Permission) -> bool:
    """Whether ``principal`` holds ``permission`` for this request."""
    return permission in principal_permissions(principal)


def principal_scope(principal, permission: Permission) -> AccessScope | None:
    """The scope at which ``principal`` holds ``permission`` (or ``None``).

    A scoped machine credential inherits the owner's live role-scope rule but
    is capped at :data:`SCOPED_PRINCIPAL_SCOPE_CAP` (tenant): it never widens
    to the system actor, and the owner role shrinking removes the permission
    outright (the resolver re-intersects on every use).
    """
    role = Role(principal.role)
    granted = scope_rule(role, permission)
    if not is_scoped_principal(principal):
        return granted
    if granted is None:
        return None
    return cap_scope(granted, SCOPED_PRINCIPAL_SCOPE_CAP)


def scope_rule(role: Role | str, permission: Permission) -> AccessScope | None:
    """The narrowest scope at which the role holds the permission.

    An explicit :data:`ROLE_SCOPE_RULES` entry wins; a held permission
    without one gets the role's default scope; a permission the role does
    not hold returns ``None`` (unheld/unknown — callers must check
    :func:`role_has` or treat ``None`` as "no access").
    """
    resolved = Role(role)
    explicit = ROLE_SCOPE_RULES[resolved].get(permission)
    if explicit is not None:
        return explicit
    if permission not in ROLE_PERMISSIONS[resolved]:
        return None
    return _default_scope(resolved)

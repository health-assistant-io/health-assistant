"""Permission catalog, role defaults, and scope taxonomy (ADR 0054)."""

import pytest

from app.core.authz import Permission
from app.core.authz import roles as authz_roles
from app.core.authz.permissions import PERMISSION_GROUPS, iter_permissions_by_resource
from app.core.authz.policy import RouteClass, policy, route_policy_of
from app.core.authz.roles import ROLE_PERMISSIONS, permissions_for, role_has, roles_with, scope_rule
from app.core.authz.scopes import AccessScope, effective_scope, scope_covers
from app.models.enums import Role

ALL_ROLES = [Role.SYSTEM_ADMIN, Role.ADMIN, Role.MANAGER, Role.USER]


def _all_permissions() -> set[Permission]:
    return set(Permission)


def test_permission_values_are_resource_action():
    for permission in Permission:
        resource, action = permission.value.split(":", 1)
        assert resource == resource.lower()
        assert action == action.lower()
        assert permission.resource() == resource
        assert permission.action() == action


def test_permission_groups_cover_the_catalog_exactly():
    grouped = {permission for group in PERMISSION_GROUPS.values() for permission in group}
    assert grouped == _all_permissions()
    listed = [permission for _, group in iter_permissions_by_resource() for permission in group]
    assert len(listed) == len(_all_permissions())


def test_catalog_covers_every_declared_resource_family():
    resources = {permission.resource() for permission in Permission}
    assert resources == {
        "tenant",
        "user",
        "patient",
        "clinical",
        "catalog",
        "document",
        "export",
        "import",
        "ai",
        "integration",
        "source",
        "seed_source",
        "audit",
        "lifecycle",
        "settings",
        "break_glass",
    }


def test_system_admin_holds_everything():
    assert ROLE_PERMISSIONS[Role.SYSTEM_ADMIN] == _all_permissions()


def test_admin_holds_every_manager_and_user_permission():
    admin = ROLE_PERMISSIONS[Role.ADMIN]
    manager = ROLE_PERMISSIONS[Role.MANAGER]
    user = ROLE_PERMISSIONS[Role.USER]
    assert manager <= admin
    assert user <= admin


def test_deletes_are_admin_tier_only():
    """Phase-0 tightening of the draft matrix (ADR 0054)."""
    for permission in (Permission.PATIENT_DELETE, Permission.CLINICAL_DELETE):
        assert roles_with(permission) == (Role.SYSTEM_ADMIN, Role.ADMIN)


def test_export_patient_is_distinct_from_read_grants():
    """export:patient is its own permission — holding patient:read never
    implies it (the authorize() check in Phase 1b requires the export
    permission explicitly, per ADR 0054 D5)."""
    assert Permission.EXPORT_PATIENT is not Permission.PATIENT_READ
    assert Role.USER in roles_with(Permission.EXPORT_PATIENT)
    assert scope_rule(Role.USER, Permission.EXPORT_PATIENT) == AccessScope.OWN


def test_system_only_permissions():
    assert set(roles_with(Permission.TENANT_CREATE)) == {Role.SYSTEM_ADMIN}
    assert set(roles_with(Permission.TENANT_SWITCH)) == {Role.SYSTEM_ADMIN}
    assert set(roles_with(Permission.SOURCE_MANAGE)) == {Role.SYSTEM_ADMIN}
    assert set(roles_with(Permission.AUDIT_READ)) == {Role.ADMIN, Role.SYSTEM_ADMIN}


def test_break_glass_defaults():
    assert roles_with(Permission.BREAK_GLASS_REQUEST) == (
        Role.SYSTEM_ADMIN,
        Role.ADMIN,
        Role.MANAGER,
    )
    assert roles_with(Permission.BREAK_GLASS_APPROVE) == (Role.SYSTEM_ADMIN, Role.ADMIN)
    assert "break_glass" not in {role.value.lower() for role in Role}


def test_system_admin_has_no_narrowing_rules():
    assert (
        ROLE_PERMISSIONS[Role.SYSTEM_ADMIN]
        and authz_roles.ROLE_SCOPE_RULES[Role.SYSTEM_ADMIN] == {}
    )


def test_helpers_agree_with_the_table():
    assert permissions_for(Role.USER) == ROLE_PERMISSIONS[Role.USER]
    assert permissions_for("USER") == ROLE_PERMISSIONS[Role.USER]
    assert role_has(Role.ADMIN, Permission.USER_INVITE)
    assert not role_has(Role.USER, Permission.USER_INVITE)
    assert Role.USER in roles_with(Permission.AI_CONFIG_WRITE)
    assert scope_rule(Role.ADMIN, Permission.EXPORT_SYSTEM) == AccessScope.TENANT
    assert scope_rule(Role.USER, Permission.AI_CONFIG_WRITE) == AccessScope.OWN
    assert scope_rule(Role.MANAGER, Permission.PATIENT_READ) == AccessScope.TENANT


def test_scope_rule_is_none_for_unheld_permissions():
    """An unheld/unknown permission is ``None``, never a borrowed default —
    a caller must not read a tenant-wide answer for a permission the role
    does not hold."""
    assert scope_rule(Role.USER, Permission.USER_INVITE) is None
    assert scope_rule(Role.USER, Permission.AUDIT_READ) is None
    assert scope_rule(Role.MANAGER, Permission.PATIENT_DELETE) is None
    assert scope_rule(Role.MANAGER, Permission.TENANT_CREATE) is None
    assert scope_rule(Role.ADMIN, Permission.TENANT_CREATE) is None
    assert scope_rule(Role.USER, Permission.BREAK_GLASS_APPROVE) is None


def test_manager_promotes_catalog_scopes_user_tenant():
    """ADR 0054 Phase-6 revise: MANAGER holds ``catalog:promote`` at the
    role default (tenant) — ``CatalogAccessPolicy.check_promote`` and the
    promote UI both advertise MANAGER user↔tenant transitions, so the gate
    must not be stricter than the policy below it. USER holds no promote
    permission (403 at the gate); ADMIN keeps tenant scope without an
    explicit rule and SYSTEM_ADMIN resolves to system."""
    assert role_has(Role.MANAGER, Permission.CATALOG_PROMOTE)
    assert scope_rule(Role.MANAGER, Permission.CATALOG_PROMOTE) == AccessScope.TENANT
    assert authz_roles.ROLE_SCOPE_RULES[Role.MANAGER].get(Permission.CATALOG_PROMOTE) is None
    assert not role_has(Role.USER, Permission.CATALOG_PROMOTE)
    assert scope_rule(Role.USER, Permission.CATALOG_PROMOTE) is None
    assert scope_rule(Role.ADMIN, Permission.CATALOG_PROMOTE) == AccessScope.TENANT
    assert authz_roles.ROLE_SCOPE_RULES[Role.ADMIN].get(Permission.CATALOG_PROMOTE) is None
    assert scope_rule(Role.SYSTEM_ADMIN, Permission.CATALOG_PROMOTE) == AccessScope.SYSTEM


def test_catalog_promote_policy_agrees_with_the_role_grant():
    """The gate's MANAGER grant mirrors the row-level promote policy:
    MANAGER passes ``CatalogAccessPolicy.check_promote`` for user↔tenant
    (and never for system), USER is refused there too — one answer at both
    layers."""
    from app.catalogs.policy import DEFAULT_CATALOG_POLICY, CatalogPermissionDenied

    policy = DEFAULT_CATALOG_POLICY
    policy.check_promote(Role.MANAGER, "user", "tenant")
    policy.check_promote(Role.MANAGER, "tenant", "user")
    with pytest.raises(CatalogPermissionDenied):
        policy.check_promote(Role.MANAGER, "tenant", "system")
    with pytest.raises(CatalogPermissionDenied):
        policy.check_promote(Role.USER, "user", "tenant")
    policy.check_promote(Role.ADMIN, "user", "tenant")
    policy.check_promote(Role.SYSTEM_ADMIN, "tenant", "system")


def test_scope_rule_default_applies_only_to_held_permissions():
    """Held permissions without an explicit rule resolve to the role default."""
    assert scope_rule(Role.USER, Permission.PATIENT_READ) == AccessScope.OWN
    assert scope_rule(Role.MANAGER, Permission.CLINICAL_READ) == AccessScope.TENANT
    assert scope_rule(Role.ADMIN, Permission.PATIENT_READ) == AccessScope.TENANT
    assert scope_rule(Role.SYSTEM_ADMIN, Permission.PATIENT_READ) == AccessScope.SYSTEM


def test_manager_patient_and_export_rules_are_the_explicit_tenant_default():
    """ADR 0054 follow-up: the static rule is the draft-matrix default —
    MANAGER patient read/write and patient export are tenant-wide, and the
    ``manager_scope=tenant|assigned`` tenant setting narrows them at request
    time (Phase-2 grant filter). The knob is never encoded here, and the
    rule is explicit, not a silent fallback."""
    for permission in (
        Permission.PATIENT_READ,
        Permission.PATIENT_WRITE,
        Permission.EXPORT_PATIENT,
    ):
        assert authz_roles.ROLE_SCOPE_RULES[Role.MANAGER].get(permission) is AccessScope.TENANT
        assert scope_rule(Role.MANAGER, permission) == AccessScope.TENANT


def test_manager_integration_rules_are_explicitly_own():
    """ADR 0054 follow-up: integrations are per-user (user_integrations),
    so MANAGER holds connect/consent at OWN scope — never the TENANT
    default — and USER keeps OWN without inheriting any manager scope."""
    for permission in (Permission.INTEGRATION_CONNECT, Permission.INTEGRATION_CONSENT):
        assert authz_roles.ROLE_SCOPE_RULES[Role.MANAGER].get(permission) is AccessScope.OWN
        assert scope_rule(Role.MANAGER, permission) == AccessScope.OWN
        assert scope_rule(Role.USER, permission) == AccessScope.OWN


def test_scope_lattice_containment():
    assert scope_covers(AccessScope.GRANT, AccessScope.GRANT)
    assert scope_covers(AccessScope.GRANT, AccessScope.OWN)
    assert scope_covers(AccessScope.OWN, AccessScope.TENANT)
    assert scope_covers(AccessScope.TENANT, AccessScope.SYSTEM)
    assert not scope_covers(AccessScope.TENANT, AccessScope.OWN)
    assert not scope_covers(AccessScope.OWN, AccessScope.GRANT)
    assert scope_covers(AccessScope.SYSTEM, AccessScope.BREAK_GLASS)


def test_break_glass_is_not_a_role_permission_of_its_own():
    """The widening lives in access_elevations (ADR 0054 D1), not in roles."""
    assert AccessScope.BREAK_GLASS not in {
        scope_rule(role, permission)
        for role in ALL_ROLES
        for permission in ROLE_PERMISSIONS[role]
        if permission in ROLE_PERMISSIONS[role]
    }


def test_effective_scope_widens_only_under_elevation():
    assert effective_scope(AccessScope.OWN, elevated=False) is AccessScope.OWN
    assert effective_scope(AccessScope.OWN, elevated=True) is AccessScope.BREAK_GLASS


def test_policy_decorator_is_inert_metadata():
    @policy(RouteClass.SESSION, permission=Permission.PATIENT_READ, scope=AccessScope.GRANT)
    def get_patient():
        return {"ok": True}

    declared = route_policy_of(get_patient)
    assert declared is not None
    assert declared.route_class is RouteClass.SESSION
    assert declared.permission is Permission.PATIENT_READ
    assert declared.scope is AccessScope.GRANT
    assert get_patient() == {"ok": True}

    with_a_class = policy(RouteClass.TOKEN_FHIR)

    def facade_read():
        return None

    assert route_policy_of(with_a_class(facade_read)).route_class is RouteClass.TOKEN_FHIR


def test_double_declaration_is_refused():
    decorated = policy(RouteClass.SESSION)

    def endpoint():
        return None

    once = decorated(endpoint)
    with pytest.raises(ValueError):
        decorated(once)


def test_route_class_vocabulary_is_closed():
    assert {cls.value for cls in RouteClass} == {
        "session",
        "public_auth",
        "public",
        "token_fhir",
        "token_oauth",
        "token_presign",
        "tokenless_hmac",
        "token_scim",
        "websocket",
    }

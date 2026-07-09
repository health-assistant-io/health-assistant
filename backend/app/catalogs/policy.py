"""Uniform, ownership-based access policy for all clinical catalogs.

Every catalog item carries an explicit :class:`~app.models.enums.CatalogScope`
(``system`` | ``tenant`` | ``user``). Access is decided by the caller's role +
the item's scope + ownership — not by a per-item ACL matrix. Plan §1.

Conventions (the CRUD matrix from plan §1.2):

+----------+------------------+-----------------+--------------------------+
| Action   | system scope     | tenant scope    | user scope               |
+----------+------------------+-----------------+--------------------------+
| Read     | everyone         | tenant members  | tenant members           |
| Create   | SYSTEM_ADMIN     | ADMIN / MANAGER | ANY authenticated user   |
| Update   | SYSTEM_ADMIN     | ADMIN / MANAGER | creator OR ADMIN         |
| Delete   | SYSTEM_ADMIN     | ADMIN / MANAGER | creator OR ADMIN         |
+----------+------------------+-----------------+--------------------------+

A create by a given role *lands* in a scope derived from that role
(SYSTEM_ADMIN→system, ADMIN/MANAGER→tenant, USER→user). Promotions/demotions
are role-gated transitions between scopes (plan §1.3).

``role`` arrives from the JWT as a plain ``str`` (``TokenData.role``).
:class:`~app.models.enums.Role` is a ``str`` enum, so ``Role.USER == "USER"``
holds and the comparisons below accept either form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union
from uuid import UUID

from app.models.enums import CatalogScope, Role

RoleLike = Union[Role, str]
ScopeLike = Union[CatalogScope, str]


class CatalogPermissionDenied(Exception):
    """Raised when a role/scope combination is not allowed.

    Endpoints translate this to HTTP 403 (the same mapping the concept
    endpoints already use).
    """


def _norm(role: RoleLike) -> str:
    return role.value if isinstance(role, Role) else str(role)


def _scope_norm(scope: Optional[ScopeLike]) -> str:
    if scope is None:
        return CatalogScope.SYSTEM.value
    return scope.value if isinstance(scope, CatalogScope) else str(scope)


def _uuid_eq(a, b) -> bool:
    """Compare two UUID-ish values safely (tolerates None / str / UUID)."""
    if a is None or b is None:
        return False
    try:
        return str(a) == str(b)
    except Exception:
        return False


@dataclass(frozen=True)
class CatalogAccessPolicy:
    """Declarative, scope + ownership RBAC for a single catalog type."""

    read_roles: tuple[RoleLike, ...] = (
        Role.USER,
        Role.MANAGER,
        Role.ADMIN,
        Role.SYSTEM_ADMIN,
    )
    # Roles that may create at the tenant tier (and promote user→tenant).
    tenant_write_roles: tuple[RoleLike, ...] = (Role.MANAGER, Role.ADMIN)

    # --- reads ------------------------------------------------------------

    def can_read(self, role: RoleLike) -> bool:
        return _norm(role) in {_norm(r) for r in self.read_roles}

    def check_read(self, role: RoleLike) -> None:
        if not self.can_read(role):
            raise CatalogPermissionDenied("read forbidden for this role")

    # --- create -----------------------------------------------------------

    def create_scope(self, role: RoleLike) -> CatalogScope:
        """The scope a *new* item gets when created by this role.

        SYSTEM_ADMIN → system, ADMIN/MANAGER → tenant, USER → user. Any
        authenticated role may create — the scope is what varies. This is the
        "users can contribute without breaking curated data" insight.
        """
        norm = _norm(role)
        if norm == Role.SYSTEM_ADMIN.value:
            return CatalogScope.SYSTEM
        if norm in {_norm(r) for r in self.tenant_write_roles}:
            return CatalogScope.TENANT
        return CatalogScope.USER

    def assign_create_scope(
        self,
        role: RoleLike,
        obj,
        actor_tenant_id: Optional[UUID],
        actor_user_id: Optional[UUID],
    ) -> CatalogScope:
        """Validate the create + stamp scope/tenant_id/created_by on ``obj``.

        - system  → ``tenant_id = None`` (canonical), ``created_by`` left unset.
        - tenant  → ``tenant_id = actor_tenant_id``, ``created_by = actor_user_id``.
        - user    → ``tenant_id = actor_tenant_id``, ``created_by = actor_user_id``.
        """
        scope = self.create_scope(role)
        obj.scope = scope
        if scope is CatalogScope.SYSTEM:
            obj.tenant_id = None
        else:
            obj.tenant_id = actor_tenant_id
            if actor_user_id is not None:
                obj.created_by = actor_user_id
        return scope

    # --- update / delete --------------------------------------------------

    def check_modify(
        self,
        role: RoleLike,
        item_scope: Optional[ScopeLike],
        *,
        item_created_by: Optional[UUID] = None,
        actor_user_id: Optional[UUID] = None,
    ) -> None:
        """Ownership-aware update/delete gate (plan §1.2)."""
        norm = _norm(role)
        if norm == Role.SYSTEM_ADMIN.value:
            return  # superuser bypass
        scope = _scope_norm(item_scope)
        if scope == CatalogScope.SYSTEM.value:
            raise CatalogPermissionDenied(
                "only SYSTEM_ADMIN may modify system-scope catalog rows"
            )
        if scope == CatalogScope.TENANT.value:
            if norm not in {_norm(r) for r in self.tenant_write_roles}:
                raise CatalogPermissionDenied(
                    "modifying tenant-scope rows requires ADMIN or MANAGER"
                )
            return
        # USER scope: creator OR ADMIN/MANAGER of the tenant.
        if norm in {_norm(r) for r in self.tenant_write_roles}:
            return
        if _uuid_eq(item_created_by, actor_user_id):
            return
        raise CatalogPermissionDenied(
            "you can only modify your own user-scope catalog entries"
        )

    # --- promote / demote -------------------------------------------------

    def check_promote(
        self,
        role: RoleLike,
        from_scope: Optional[ScopeLike],
        to_scope: ScopeLike,
    ) -> None:
        """Gate a scope transition (plan §1.3).

        - user → tenant : ADMIN / MANAGER
        - tenant → user : ADMIN / MANAGER  (demotion)
        - anything involving system : SYSTEM_ADMIN only
        Same-scope is a no-op (allowed for any role that could modify).
        """
        norm = _norm(role)
        if norm == Role.SYSTEM_ADMIN.value:
            return  # superuser bypass
        f = _scope_norm(from_scope)
        t = _scope_norm(to_scope)
        if f == t:
            return
        tenant_roles = {_norm(r) for r in self.tenant_write_roles}
        if (f, t) in {
            (CatalogScope.USER.value, CatalogScope.TENANT.value),
            (CatalogScope.TENANT.value, CatalogScope.USER.value),
        }:
            if norm in tenant_roles:
                return
            raise CatalogPermissionDenied(
                "this scope transition requires ADMIN or MANAGER"
            )
        raise CatalogPermissionDenied(
            "this scope transition requires SYSTEM_ADMIN"
        )


DEFAULT_CATALOG_POLICY = CatalogAccessPolicy()

"""The central route policy resolver — the declaration of every route's authz.

ADR 0054 (Phase 1b): the rules below ARE the declaration. Every session
route must resolve to a permission + required :class:`AccessScope`, or be an
explicit self-service auth-only route; every ``public``/``public_auth``/
``token_*``/``websocket`` route resolves to its class (no permission — the
class's own mechanism enforces, see
:data:`app.core.authz.route_gate.ROUTE_CLASS_DEPENDENCIES`). A route that
resolves to nothing is denied (fail closed) and fails the CI gate
(``scripts/authz_inventory.py check``).

``route_inventory.json`` stays the route-class baseline (one reviewed entry
per route); it is not a dumping ground — the permission/scope decision lives
in these rules, and the gate cross-checks both (ADR 0054, implementation
note).

Rule grammar (matched in order, first match wins):

- ``path`` is the served templated path with the ``/api/v1`` (or
  ``/api/v2``) prefix stripped; ``{param}`` matches exactly one segment,
  ``{param:path}`` matches the remainder. Rules are ordered so specific
  paths precede the generic shapes that would shadow them.
- ``methods`` pins the HTTP methods the rule covers; a request whose method
  group (read/write/delete) has no policy does not match the rule.
  ``HEAD`` normalizes to ``GET``.
- ``read``/``write``/``delete`` carry the ``(permission, required scope)``
  for GET/HEAD, POST/PUT/PATCH and DELETE respectively.

Scope requirements follow the reviewed role matrix: ``GRANT`` on
patient-scoped and per-record resource paths (the weakest "reach the
resource through a legitimate relationship" bar — the row-level
ownership/grant check stays with the resource layer), ``TENANT`` on
tenant-wide lists and admin surfaces, ``SYSTEM`` on cross-tenant/global
administration, ``OWN`` where the surface is explicitly the caller's own
records. Destructive deletes are their own permission per D1
(``PATIENT_DELETE`` / ``CLINICAL_DELETE`` are ADMIN-side in the baseline).

Catalog row doors (create/update/delete of one catalog row) floor at
``catalog:create``: the unified write contract and the catalog access
policy own the per-row semantics (a creator edits their own user-scope
rows, staff moderate tenant rows, system rows are never writable), and the
gate must not deny the creator before that policy runs.

Surfaces the closed catalog does not enumerate (per-user notification
inbox/rules, onboarding checklist, tenant profile reads, settings
self-service) are **auth-only self-service**: they act on the caller's own
session-scoped data and require no permission beyond the session — the same
principle as the ``/me/*`` identity surface. Everything else must carry a
permission.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache

from app.core.authz.permissions import Permission
from app.core.authz.policy import RouteClass, RoutePolicy
from app.core.authz.scopes import AccessScope

type _Policy = tuple[Permission, AccessScope] | None

_API_PREFIXES = ("/api/v1", "/api/v2")

_PARAM_RE = re.compile(r"\{(\w+)(:path)?\}")

_READ_METHODS = frozenset({"GET"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
_POST_ONLY = frozenset({"POST"})
_PUT_ONLY = frozenset({"PUT"})
_PATCH_ONLY = frozenset({"PATCH"})
_DELETE_ONLY = frozenset({"DELETE"})
_ALL_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class RouteRule:
    """One ordered resolver rule: a path shape, a class, and its policies."""

    name: str
    path: str
    route_class: RouteClass = RouteClass.SESSION
    methods: frozenset[str] | None = None
    read: _Policy = None
    write: _Policy = None
    delete: _Policy = None
    auth_only: bool = False


def _p(permission: Permission, scope: AccessScope) -> _Policy:
    return (permission, scope)


def _rule(
    name: str,
    path: str,
    methods: frozenset[str] | None,
    *,
    read: _Policy = None,
    write: _Policy = None,
    delete: _Policy = None,
    route_class: RouteClass = RouteClass.SESSION,
    auth_only: bool = False,
) -> RouteRule:
    """Build one rule (a constructor keeps the table below scannable)."""
    return RouteRule(
        name=name,
        path=path,
        route_class=route_class,
        methods=methods,
        read=read,
        write=write,
        delete=delete,
        auth_only=auth_only,
    )


def _auth_only(name: str, path: str, methods: frozenset[str] | None = None) -> RouteRule:
    return _rule(name, path, methods, auth_only=True)


def _class_rule(name: str, path: str, route_class: RouteClass) -> RouteRule:
    return _rule(name, path, None, route_class=route_class, auth_only=True)


def _clinical_family_rules(family: str) -> Iterator[RouteRule]:
    """The shared catalog + patient-instance shapes of allergies/medications/vaccines.

    Catalog rows are ``CATALOG_*`` (``MODERATE`` for edits/deletes), patient
    instance rows are ``CLINICAL_*`` — with per-family rule names so the
    generated table names the surface, not a wildcard family.
    """
    yield _rule(
        f"{family}-catalog",
        f"/{family}/catalog",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    )
    yield _rule(
        f"{family}-catalog-item",
        f"/{family}/catalog/{{catalog_id}}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    )
    yield _rule(
        f"{family}-catalog-usage",
        f"/{family}/catalog/{{catalog_id}}/usage",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    )
    yield _rule(
        f"{family}-catalog-reprocess",
        f"/{family}/catalog/{{catalog_id}}/reprocess",
        _POST_ONLY,
        write=_p(Permission.CATALOG_MODERATE, AccessScope.GRANT),
    )
    yield _rule(
        f"{family}-patient-collection",
        f"/{family}/patient/{{patient_id}}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    )
    yield _rule(
        f"{family}-item",
        f"/{family}/{{item_id}}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    )


#: Ordered rules — first match wins; specific paths precede generic shapes.
RULES: tuple[RouteRule, ...] = (
    # --- identity self-service (session auth only) --------------------------
    _auth_only("auth-me", "/auth/me", _READ_METHODS),
    _auth_only("auth-validate", "/auth/validate", _READ_METHODS),
    _auth_only("auth-logout", "/auth/logout", _WRITE_METHODS),
    _auth_only("auth-logout-all", "/auth/logout-all", _WRITE_METHODS),
    _auth_only("auth-step-up", "/auth/step-up", _POST_ONLY),
    _auth_only("auth-step-up-passkeys-options", "/auth/step-up/passkeys/options", _POST_ONLY),
    _auth_only("auth-step-up-passkeys-verify", "/auth/step-up/passkeys/verify", _POST_ONLY),
    _auth_only("me-self-service", "/me/{rest:path}", _ALL_METHODS),
    _auth_only("integration-principals-collection", "/integration-principals", _ALL_METHODS),
    _auth_only("integration-principals", "/integration-principals/{rest:path}", _ALL_METHODS),
    _rule(
        "auth-invite",
        "/auth/invite",
        _POST_ONLY,
        write=_p(Permission.USER_INVITE, AccessScope.TENANT),
    ),
    _auth_only("auth-sso-link", "/auth/sso/link", _POST_ONLY),
    _auth_only("setup-self-service", "/setup/{rest:path}", _ALL_METHODS),
    _auth_only("tenant-profile-read", "/tenants", _READ_METHODS),
    _auth_only("tenant-profile-read-id", "/tenants/{tenant_id}", _READ_METHODS),
    _rule(
        "tenant-security-policy",
        "/tenants/{tenant_id}/security-policy",
        frozenset({"GET", "PATCH"}),
        read=_p(Permission.SETTINGS_READ, AccessScope.TENANT),
        write=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
    ),
    _rule(
        "tenant-self-update",
        "/tenants/{tenant_id}",
        _PATCH_ONLY,
        write=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
    ),
    # --- settings ------------------------------------------------------------
    _auth_only("settings-user-self", "/settings/user", _READ_METHODS | {"PUT"}),
    _auth_only("settings-effective", "/settings/effective", _READ_METHODS),
    _auth_only("settings-definitions", "/settings/definitions", _READ_METHODS),
    _rule(
        "settings-system",
        "/settings/system",
        frozenset({"GET", "PUT"}),
        read=_p(Permission.SETTINGS_READ, AccessScope.SYSTEM),
        write=_p(Permission.SETTINGS_WRITE, AccessScope.SYSTEM),
    ),
    _rule(
        "settings-tenant",
        "/settings/tenant",
        frozenset({"GET", "PUT"}),
        read=_p(Permission.SETTINGS_READ, AccessScope.TENANT),
        write=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
    ),
    # --- notifications: tenant-admin surface first, user self-service after --
    _rule(
        "notifications-admin-feed",
        "/notifications/admin",
        _READ_METHODS,
        read=_p(Permission.TENANT_READ, AccessScope.TENANT),
    ),
    _rule(
        "notifications-admin-stats",
        "/notifications/admin/stats",
        _READ_METHODS,
        read=_p(Permission.TENANT_READ, AccessScope.TENANT),
    ),
    _rule(
        "notifications-admin-delivery",
        "/notifications/admin/{notification_id}/delivery",
        _READ_METHODS,
        read=_p(Permission.TENANT_READ, AccessScope.TENANT),
    ),
    _auth_only("notifications-self-service", "/notifications/{rest:path}"),
    _auth_only("notification-rules-self-service", "/notification-rules", _ALL_METHODS),
    _auth_only(
        "notification-rules-item-self-service", "/notification-rules/{rest:path}", _ALL_METHODS
    ),
    # --- patients ------------------------------------------------------------
    _rule(
        "patients-list",
        "/patients",
        _READ_METHODS,
        read=_p(Permission.PATIENT_READ, AccessScope.OWN),
    ),
    _rule(
        "patients-create",
        "/patients",
        _POST_ONLY,
        write=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "patients-item",
        "/patients/{patient_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.PATIENT_READ, AccessScope.GRANT),
        write=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
        delete=_p(Permission.PATIENT_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "patients-access",
        "/patients/{patient_id}/access",
        frozenset({"GET", "POST"}),
        read=_p(Permission.PATIENT_GRANT, AccessScope.GRANT),
        write=_p(Permission.PATIENT_GRANT, AccessScope.GRANT),
    ),
    _rule(
        "patients-access-item",
        "/patients/{patient_id}/access/{grant_id}",
        frozenset({"PATCH", "DELETE"}),
        write=_p(Permission.PATIENT_GRANT, AccessScope.GRANT),
        delete=_p(Permission.PATIENT_GRANT, AccessScope.GRANT),
    ),
    _rule(
        "patients-layout",
        "/patients/{patient_id}/layout",
        _PUT_ONLY,
        write=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "patient-layouts",
        "/patients/{patient_id}/layouts",
        frozenset({"GET", "POST"}),
        read=_p(Permission.PATIENT_READ, AccessScope.GRANT),
        write=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "patient-layouts-active",
        "/patients/{patient_id}/layouts/active",
        _READ_METHODS,
        read=_p(Permission.PATIENT_READ, AccessScope.GRANT),
    ),
    _rule(
        "patient-layouts-item",
        "/patients/{patient_id}/layouts/{layout_id}",
        frozenset({"PUT", "DELETE"}),
        write=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
        delete=_p(Permission.PATIENT_WRITE, AccessScope.GRANT),
    ),
    # --- clinical record family ----------------------------------------------
    _rule(
        "examinations-bulk-delete",
        "/examinations/bulk-delete",
        _POST_ONLY,
        write=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "examinations-categories",
        "/examinations/categories",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "examinations-collection",
        "/examinations",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "examinations-item",
        "/examinations/{examination_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "examinations-item-action",
        "/examinations/{examination_id}/{action}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "observations-collection",
        "/observations",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "observations-item",
        "/observations/{observation_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    *_clinical_family_rules("allergies"),
    *_clinical_family_rules("medications"),
    *_clinical_family_rules("vaccines"),
    # clinical events: journeys + their type catalog
    _rule(
        "clinical-events-types",
        "/clinical-events/types",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-type-biomarkers-item",
        "/clinical-events/types/{type_id}/biomarkers/{biomarker_id}",
        _DELETE_ONLY,
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-type-biomarkers",
        "/clinical-events/types/{type_id}/biomarkers",
        frozenset({"GET", "POST", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_MODERATE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-occurrence",
        "/clinical-events/{event_id}/occurrences/{occurrence_id}",
        frozenset({"POST", "DELETE"}),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-item",
        "/clinical-events/{event_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-unlink-anatomy",
        "/clinical-events/{event_id}/unlink-anatomy/{anatomy_id}",
        _DELETE_ONLY,
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-item-action",
        "/clinical-events/{event_id}/{action}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "clinical-events-collection",
        "/clinical-events",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    # documents (presigned download is its own token_presign class)
    _rule(
        "documents-preview-temp",
        "/documents/preview-temp",
        _POST_ONLY,
        write=_p(Permission.DOCUMENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "documents-collection",
        "/documents",
        frozenset({"GET", "POST"}),
        read=_p(Permission.DOCUMENT_READ, AccessScope.GRANT),
        write=_p(Permission.DOCUMENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "documents-presign",
        "/documents/{document_id}/presign",
        _POST_ONLY,
        write=_p(Permission.DOCUMENT_PRESIGN, AccessScope.GRANT),
    ),
    _rule(
        "documents-extract-status",
        "/documents/{document_id}/extract/status",
        _READ_METHODS,
        read=_p(Permission.DOCUMENT_READ, AccessScope.GRANT),
    ),
    _rule(
        "documents-item-read-action",
        "/documents/{document_id}/{action}",
        _READ_METHODS,
        read=_p(Permission.DOCUMENT_READ, AccessScope.GRANT),
    ),
    _rule(
        "documents-item-write-action",
        "/documents/{document_id}/{action}",
        _POST_ONLY,
        write=_p(Permission.DOCUMENT_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "documents-item",
        "/documents/{document_id}",
        frozenset({"GET", "PATCH", "DELETE"}),
        read=_p(Permission.DOCUMENT_READ, AccessScope.GRANT),
        write=_p(Permission.DOCUMENT_WRITE, AccessScope.GRANT),
        delete=_p(Permission.DOCUMENT_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "instances-search",
        "/instances/search",
        _READ_METHODS,
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
    ),
    _rule(
        "search",
        "/search",
        _READ_METHODS,
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
    ),
    _rule(
        "analytics",
        "/analytics/{rest:path}",
        _READ_METHODS,
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
    ),
    _rule(
        "task-monitor",
        "/task-monitor/{rest:path}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "doctors-collection",
        "/doctors",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    _rule(
        "doctors-item",
        "/doctors/{doctor_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.GRANT),
    ),
    _rule(
        "organizations-collection",
        "/organizations",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.TENANT),
    ),
    _rule(
        "organizations-item",
        "/organizations/{organization_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
        write=_p(Permission.CLINICAL_WRITE, AccessScope.TENANT),
        delete=_p(Permission.CLINICAL_DELETE, AccessScope.TENANT),
    ),
    # --- catalogs (meta-layer + biomarkers + concepts + anatomy) --------------
    _rule(
        "catalogs-search",
        "/catalogs/search",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "catalogs-collection",
        "/catalogs",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "catalogs-type",
        "/catalogs/{type}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "catalogs-promote",
        "/catalogs/{type}/{item_id}/promote",
        _POST_ONLY,
        write=_p(Permission.CATALOG_PROMOTE, AccessScope.TENANT),
    ),
    _rule(
        "catalogs-item-read-action",
        "/catalogs/{type}/{item_id}/{action}",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "catalogs-item",
        "/catalogs/{type}/{item_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-units",
        "/biomarkers/units",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-states",
        "/biomarkers/states",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-bulk-delete",
        "/biomarkers/bulk-delete",
        _POST_ONLY,
        write=_p(Permission.CATALOG_MODERATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-slug",
        "/biomarkers/slug/{slug}",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "biomarker-ranges",
        "/biomarkers/{biomarker_id}/reference-ranges",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarker-range-item",
        "/biomarkers/{biomarker_id}/reference-ranges/{range_id}",
        frozenset({"PUT", "DELETE"}),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-migration-action",
        "/biomarkers/{biomarker_id}/{action}",
        _POST_ONLY,
        write=_p(Permission.CATALOG_MODERATE, AccessScope.TENANT),
    ),
    _rule(
        "biomarkers-collection",
        "/biomarkers/",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "biomarkers-item",
        "/biomarkers/{biomarker_id}",
        frozenset({"GET", "PUT", "PATCH", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "concepts-search",
        "/concepts/search",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "concepts-restore",
        "/concepts/{concept_id}/restore",
        _POST_ONLY,
        write=_p(Permission.CATALOG_MODERATE, AccessScope.GRANT),
    ),
    _rule(
        "concepts-item-read-action",
        "/concepts/{concept_id}/{action}",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "concepts-collection",
        "/concepts",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "concepts-item",
        "/concepts/{concept_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "concept-edges-item",
        "/concept-edges/{edge_id}",
        _DELETE_ONLY,
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "concept-edges",
        "/concept-edges",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-figures",
        "/anatomy/figures",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-figure-media",
        "/anatomy/figures/{slug}/{media}",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-figure-item",
        "/anatomy/figures/{slug}",
        frozenset({"GET", "PATCH", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-import",
        "/anatomy/import",
        _POST_ONLY,
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-relations",
        "/anatomy/relations",
        _POST_ONLY,
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-item-read-action",
        "/anatomy/{identifier}/{action}",
        _READ_METHODS,
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-collection",
        "/anatomy",
        frozenset({"GET", "POST"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    _rule(
        "anatomy-item",
        "/anatomy/{identifier}",
        frozenset({"GET", "PATCH", "DELETE"}),
        read=_p(Permission.CATALOG_READ, AccessScope.GRANT),
        write=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
        delete=_p(Permission.CATALOG_CREATE, AccessScope.GRANT),
    ),
    # --- AI -------------------------------------------------------------------
    _rule(
        "ai-assistance",
        "/ai-assistance/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.AI_USE, AccessScope.GRANT),
        write=_p(Permission.AI_USE, AccessScope.GRANT),
        delete=_p(Permission.AI_USE, AccessScope.GRANT),
    ),
    _rule(
        "ai-pricing",
        "/ai/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.AI_CONFIG_READ, AccessScope.OWN),
        write=_p(Permission.AI_CONFIG_WRITE, AccessScope.OWN),
        delete=_p(Permission.AI_CONFIG_WRITE, AccessScope.OWN),
    ),
    _rule(
        "ai-config",
        "/ai-config/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.AI_CONFIG_READ, AccessScope.OWN),
        write=_p(Permission.AI_CONFIG_WRITE, AccessScope.OWN),
        delete=_p(Permission.AI_CONFIG_WRITE, AccessScope.OWN),
    ),
    # --- integrations / extensions / cloud connectors --------------------------
    _rule(
        "integration-consents",
        "/integrations/consents/{rest:path}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.INTEGRATION_CONSENT, AccessScope.OWN),
        write=_p(Permission.INTEGRATION_CONSENT, AccessScope.OWN),
    ),
    _rule(
        "integrations",
        "/integrations/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
        write=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
        delete=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
    ),
    _rule(
        "oauth-clients-collection",
        "/oauth/clients",
        frozenset({"GET", "POST"}),
        read=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
        write=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "oauth-clients",
        "/oauth/clients/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
        write=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
        delete=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "calendar",
        "/calendar/{rest:path}",
        _ALL_METHODS,
        read=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
        write=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
        delete=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
    ),
    _rule(
        "telemetry-ingest",
        "/telemetry/data",
        _POST_ONLY,
        write=_p(Permission.INTEGRATION_CONNECT, AccessScope.OWN),
    ),
    _rule(
        "extension-sources-collection",
        "/extensions/sources",
        frozenset({"GET", "POST"}),
        read=_p(Permission.SOURCE_MANAGE, AccessScope.SYSTEM),
        write=_p(Permission.SOURCE_MANAGE, AccessScope.SYSTEM),
    ),
    _rule(
        "extension-sources",
        "/extensions/sources/{rest:path}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.SOURCE_MANAGE, AccessScope.SYSTEM),
        write=_p(Permission.SOURCE_MANAGE, AccessScope.SYSTEM),
    ),
    # --- export / import --------------------------------------------------------
    _rule(
        "export-create",
        "/export",
        _POST_ONLY,
        write=_p(Permission.EXPORT_PATIENT, AccessScope.GRANT),
    ),
    _rule(
        "export-jobs",
        "/export/jobs",
        _READ_METHODS,
        read=_p(Permission.EXPORT_PATIENT, AccessScope.GRANT),
    ),
    _rule(
        "export-job-item",
        "/export/jobs/{job_id}",
        _READ_METHODS,
        read=_p(Permission.EXPORT_PATIENT, AccessScope.GRANT),
    ),
    _rule(
        "export-job",
        "/export/jobs/{job_id}/{rest:path}",
        _READ_METHODS,
        read=_p(Permission.EXPORT_PATIENT, AccessScope.GRANT),
    ),
    _rule(
        "import-fhir",
        "/import/fhir",
        _POST_ONLY,
        write=_p(Permission.IMPORT_FHIR, AccessScope.GRANT),
    ),
    _rule(
        "import-backup",
        "/import/backup",
        _POST_ONLY,
        write=_p(Permission.IMPORT_FHIR, AccessScope.TENANT),
    ),
    _rule(
        "import-jobs",
        "/import/jobs",
        _READ_METHODS,
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
    ),
    _rule(
        "import-jobs-item",
        "/import/jobs/{job_id}",
        _READ_METHODS,
        read=_p(Permission.CLINICAL_READ, AccessScope.GRANT),
    ),
    _rule(
        "import-record-entry",
        "/import/{kind}",
        _POST_ONLY,
        write=_p(Permission.CLINICAL_WRITE, AccessScope.GRANT),
    ),
    # --- data lifecycle -----------------------------------------------------------
    _rule(
        "lifecycle-erasure-create",
        "/data-lifecycle/erasures",
        _POST_ONLY,
        write=_p(Permission.LIFECYCLE_REQUEST, AccessScope.GRANT),
    ),
    _rule(
        "lifecycle-erasures",
        "/data-lifecycle/erasures",
        _READ_METHODS,
        read=_p(Permission.LIFECYCLE_REQUEST, AccessScope.TENANT),
    ),
    _rule(
        "lifecycle-erasure",
        "/data-lifecycle/erasures/{request_id}",
        _READ_METHODS,
        read=_p(Permission.LIFECYCLE_REQUEST, AccessScope.TENANT),
    ),
    _rule(
        "lifecycle-retention-policy-item",
        "/data-lifecycle/retention-policies/{policy_id}",
        _PATCH_ONLY,
        write=_p(Permission.LIFECYCLE_EXECUTE, AccessScope.SYSTEM),
    ),
    _rule(
        "lifecycle-retention-policies",
        "/data-lifecycle/retention-policies",
        frozenset({"GET", "PATCH"}),
        read=_p(Permission.LIFECYCLE_EXECUTE, AccessScope.TENANT),
        write=_p(Permission.LIFECYCLE_EXECUTE, AccessScope.SYSTEM),
    ),
    _rule(
        "lifecycle-retention-run",
        "/data-lifecycle/retention/run",
        _POST_ONLY,
        write=_p(Permission.LIFECYCLE_EXECUTE, AccessScope.SYSTEM),
    ),
    _rule(
        "lifecycle-ledger-replay",
        "/data-lifecycle/ledger/replay",
        _POST_ONLY,
        write=_p(Permission.LIFECYCLE_EXECUTE, AccessScope.SYSTEM),
    ),
    # --- users ---------------------------------------------------------------------
    _auth_only("users-self-read", "/users/me", _READ_METHODS),
    _rule(
        "users-self-erase",
        "/users/me",
        _DELETE_ONLY,
        delete=_p(Permission.LIFECYCLE_REQUEST, AccessScope.OWN),
    ),
    _rule(
        "users-collection",
        "/users",
        frozenset({"GET", "POST"}),
        read=_p(Permission.USER_READ, AccessScope.TENANT),
        write=_p(Permission.USER_INVITE, AccessScope.TENANT),
    ),
    _rule(
        "users-item-security-action",
        "/users/{user_id}/{action}",
        _POST_ONLY,
        write=_p(Permission.USER_DEACTIVATE, AccessScope.TENANT),
    ),
    _rule(
        "users-item",
        "/users/{user_id}",
        frozenset({"GET", "PUT", "DELETE"}),
        read=_p(Permission.USER_READ, AccessScope.TENANT),
        write=_p(Permission.USER_ROLE_ASSIGN, AccessScope.TENANT),
        delete=_p(Permission.USER_DEACTIVATE, AccessScope.TENANT),
    ),
    # --- instance admin ---------------------------------------------------------------
    _rule(
        "admin-system-storage",
        "/admin/system/storage",
        _READ_METHODS,
        read=_p(Permission.SETTINGS_READ, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-access-elevations-collection",
        "/admin/access-elevations",
        frozenset({"GET", "POST"}),
        read=_p(Permission.BREAK_GLASS_REQUEST, AccessScope.TENANT),
        write=_p(Permission.BREAK_GLASS_REQUEST, AccessScope.TENANT),
    ),
    _rule(
        "admin-access-elevation-action",
        "/admin/access-elevations/{elevation_id}/{action}",
        _POST_ONLY,
        # The gate door is the requester permission; the per-action authority
        # (approve/deny need the scope's approver role, revoke the requester
        # or an eligible approver) is re-asserted by the elevation service —
        # the same coarse-door/service-authority split as patient grants.
        write=_p(Permission.BREAK_GLASS_REQUEST, AccessScope.TENANT),
    ),
    _rule(
        "admin-audit",
        "/admin/audit",
        _READ_METHODS,
        read=_p(Permission.AUDIT_READ, AccessScope.TENANT),
    ),
    _rule(
        "admin-audit-export",
        "/admin/audit/export",
        _READ_METHODS,
        read=_p(Permission.AUDIT_READ, AccessScope.TENANT),
    ),
    _rule(
        "admin-audit-retention",
        "/admin/audit/retention",
        _READ_METHODS,
        read=_p(Permission.AUDIT_READ, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-audit-verify",
        "/admin/audit/verify",
        _READ_METHODS,
        read=_p(Permission.AUDIT_READ, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-seeds-export",
        "/admin/seeds/export.zip",
        _READ_METHODS,
        read=_p(Permission.SEED_SOURCE_READ, AccessScope.SYSTEM),
    ),
    _rule(
        "seed-source-version-approve",
        "/admin/seed-sources/versions/{version_id}/approve",
        _POST_ONLY,
        write=_p(Permission.SEED_SOURCE_APPROVE, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-version-reject",
        "/admin/seed-sources/versions/{version_id}/reject",
        _POST_ONLY,
        write=_p(Permission.SEED_SOURCE_APPROVE, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-version-revoke",
        "/admin/seed-sources/versions/{version_id}/revoke",
        _POST_ONLY,
        write=_p(Permission.SEED_SOURCE_APPROVE, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-version-plan",
        "/admin/seed-sources/versions/{version_id}/plan",
        _READ_METHODS,
        read=_p(Permission.SEED_SOURCE_READ, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-version-apply",
        "/admin/seed-sources/versions/{version_id}/apply",
        _POST_ONLY,
        write=_p(Permission.SEED_SOURCE_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-item",
        "/admin/seed-sources/{source_id}/{rest:path}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.SEED_SOURCE_READ, AccessScope.TENANT),
        write=_p(Permission.SEED_SOURCE_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "seed-source-collection",
        "/admin/seed-sources",
        frozenset({"GET", "POST"}),
        read=_p(Permission.SEED_SOURCE_READ, AccessScope.TENANT),
        write=_p(Permission.SEED_SOURCE_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-catalog-import",
        "/admin/catalogs/import/{rest:path}",
        _POST_ONLY,
        write=_p(Permission.CATALOG_CREATE, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-broadcast",
        "/admin/notifications/broadcast",
        _POST_ONLY,
        write=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-integrations-collection",
        "/admin/integrations",
        frozenset({"GET", "POST"}),
        read=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
        write=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-integrations",
        "/admin/integrations/{rest:path}",
        frozenset({"GET", "POST"}),
        read=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
        write=_p(Permission.INTEGRATION_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-exit-switch",
        "/admin/tenants/exit-switch",
        _POST_ONLY,
        write=_p(Permission.TENANT_SWITCH, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenants-collection",
        "/admin/tenants",
        frozenset({"GET", "POST"}),
        read=_p(Permission.TENANT_READ, AccessScope.SYSTEM),
        write=_p(Permission.TENANT_CREATE, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-tenant-audit",
        "/admin/tenants/{tenant_id}/audit",
        _READ_METHODS,
        read=_p(Permission.AUDIT_READ, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-users",
        "/admin/tenants/{tenant_id}/users",
        _READ_METHODS,
        read=_p(Permission.USER_READ, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-user-mfa",
        "/admin/tenants/{tenant_id}/users/{user_id}/mfa",
        _PATCH_ONLY,
        write=_p(Permission.USER_DEACTIVATE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-user",
        "/admin/tenants/{tenant_id}/users/{user_id}",
        _PATCH_ONLY,
        write=_p(Permission.USER_ROLE_ASSIGN, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-invite",
        "/admin/tenants/{tenant_id}/invite",
        _POST_ONLY,
        write=_p(Permission.USER_INVITE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-switch",
        "/admin/tenants/{tenant_id}/switch",
        _POST_ONLY,
        write=_p(Permission.TENANT_SWITCH, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-sso-configs",
        "/admin/tenants/{tenant_id}/sso",
        frozenset({"GET", "POST"}),
        read=_p(Permission.SETTINGS_READ, AccessScope.TENANT),
        write=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-sso-config-test",
        "/admin/tenants/{tenant_id}/sso/{config_id}/test-connection",
        _POST_ONLY,
        write=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-sso-config-item",
        "/admin/tenants/{tenant_id}/sso/{config_id}",
        frozenset({"PATCH", "DELETE"}),
        write=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
        delete=_p(Permission.SETTINGS_WRITE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-scim-tokens",
        "/admin/tenants/{tenant_id}/scim-tokens",
        frozenset({"GET", "POST"}),
        read=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
        write=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-scim-token-item",
        "/admin/tenants/{tenant_id}/scim-tokens/{token_id}",
        _DELETE_ONLY,
        delete=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
    ),
    _rule(
        "admin-tenant-lifecycle",
        "/admin/tenants/{tenant_id}/{action}",
        _POST_ONLY,
        write=_p(Permission.TENANT_MANAGE, AccessScope.SYSTEM),
    ),
    _rule(
        "admin-tenant-item",
        "/admin/tenants/{tenant_id}",
        frozenset({"GET", "PATCH", "DELETE"}),
        read=_p(Permission.TENANT_READ, AccessScope.TENANT),
        write=_p(Permission.TENANT_MANAGE, AccessScope.TENANT),
        delete=_p(Permission.TENANT_MANAGE, AccessScope.SYSTEM),
    ),
    # --- token / public principal classes (no permission; class mechanism) ------
    # Public metadata (no auth mechanism — S1 follow-ups).
    _class_rule("public-root", "/", RouteClass.PUBLIC),
    _class_rule("public-health", "/health", RouteClass.PUBLIC),
    _class_rule("public-ready", "/ready", RouteClass.PUBLIC),
    _class_rule("public-smart-config", "/.well-known/smart-configuration", RouteClass.PUBLIC),
    _class_rule("public-config", "/config/public", RouteClass.PUBLIC),
    _class_rule("public-ai-presets", "/ai-config/provider-presets", RouteClass.PUBLIC),
    _class_rule("public-edge-schema", "/concept-edges/schema", RouteClass.PUBLIC),
    _class_rule("public-vapid-key", "/notifications/vapid-public-key", RouteClass.PUBLIC),
    _class_rule("public-fhir-metadata", "/fhir/R4/metadata", RouteClass.PUBLIC),
    # Auth bootstrap (unauthenticated, rate-limited).
    _class_rule("auth-setup-status", "/auth/setup-status", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-setup", "/auth/setup", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-login", "/auth/login", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-passkeys-options", "/auth/passkeys/options", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-passkeys-verify", "/auth/passkeys/verify", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-demo-login", "/auth/demo-login", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-mfa-verify", "/auth/mfa/verify", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-mfa-enroll", "/auth/mfa/enroll", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-register", "/auth/register", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-refresh", "/auth/refresh", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-sso-authorize", "/auth/sso/authorize", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-sso-status", "/auth/sso/status", RouteClass.PUBLIC_AUTH),
    _class_rule("auth-sso-callback", "/auth/sso/callback", RouteClass.PUBLIC_AUTH),
    # Token principals.
    _class_rule("oauth-token", "/oauth/token", RouteClass.TOKEN_OAUTH),
    _class_rule("oauth-revoke", "/oauth/revoke", RouteClass.TOKEN_OAUTH),
    _class_rule("scim-v2", "/scim/v2/{rest:path}", RouteClass.TOKEN_SCIM),
    _class_rule("presign-download", "/documents/{document_id}/download", RouteClass.TOKEN_PRESIGN),
    _class_rule("fhir-facade", "/fhir/R4/{rest:path}", RouteClass.TOKEN_FHIR),
    _class_rule("integration-hmac", "/integrations/{rest:path}", RouteClass.TOKENLESS_HMAC),
    _class_rule("ws-tasks", "/ws/tasks", RouteClass.WEBSOCKET),
    _class_rule("ws-notifications", "/ws/notifications", RouteClass.WEBSOCKET),
)


def strip_api_prefix(path: str) -> str:
    """Remove the ``/api/v1`` (or ``/api/v2``) prefix from a served path."""
    for prefix in _API_PREFIXES:
        if path == prefix:
            return "/"
        if path.startswith(prefix + "/"):
            return path[len(prefix) :]
    return path


@cache
def _compiled(pattern: str) -> re.Pattern[str]:
    """Compile a rule path template into a full-match regex."""

    def _replace(match: re.Match[str]) -> str:
        return ".+" if match.group(2) else "[^/]+"

    return re.compile("^" + _PARAM_RE.sub(_replace, pattern) + "$")


def _normalize_method(method: str) -> str:
    resolved = method.upper()
    return "GET" if resolved == "HEAD" else resolved


def _group(rule: RouteRule, method: str) -> _Policy:
    if method in _READ_METHODS:
        return rule.read
    if method in _WRITE_METHODS:
        return rule.write
    if method == "DELETE":
        return rule.delete
    return None


def resolve_route_rule(
    method: str, path: str, route_class: RouteClass | str | None
) -> RouteRule | None:
    """The first rule matching ``(method, stripped path, class)``, else ``None``.

    Every route — session or class-mechanism — must match an explicit rule;
    a non-session route whose path no rule covers returns ``None`` too (the
    gate fails it closed), so a new public/token surface is a declared
    change, not an accident of the class vocabulary.
    """
    if route_class is None:
        return None
    try:
        resolved_class = RouteClass(route_class)
    except ValueError:
        return None
    stripped = strip_api_prefix(path)
    normalized = _normalize_method(method)
    for rule in RULES:
        if rule.route_class != resolved_class:
            continue
        if rule.methods is not None and normalized not in rule.methods:
            continue
        if _compiled(rule.path).match(stripped) and (
            rule.auth_only or _group(rule, normalized) is not None
        ):
            return rule
    return None


def resolve_route_policy(
    method: str, path: str, route_class: RouteClass | str | None
) -> RoutePolicy | None:
    """The declared :class:`RoutePolicy` for one route (``None`` = deny).

    Session routes resolve to a permission + required scope, or to an
    auth-only self-service rule; every other class resolves to its class
    with no permission.
    """
    if route_class is None:
        return None
    rule = resolve_route_rule(method, path, route_class)
    if rule is None:
        return None
    resolved_class = RouteClass(route_class)
    if rule.auth_only:
        return RoutePolicy(route_class=resolved_class)
    policy = _group(rule, _normalize_method(method))
    if policy is None:
        return None
    return RoutePolicy(route_class=resolved_class, permission=policy[0], scope=policy[1])

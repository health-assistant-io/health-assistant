"""Tenant security policy (ADR 0054 Phase 3 / program Phase 8 part B).

The per-tenant security knobs live in ``tenants.settings`` JSONB and are
**normalized on read, validated on write, and never cached into tokens** —
every enforcement point reads the policy for its request, so a policy flip
applies mid-session (pinned by tests).

Keys (``tenants.settings``, alongside the Phase-2 ``manager_scope`` /
``allow_patient_share`` knobs, which are part of the same policy surface):

- ``mfa_required_roles``         — subset of ``ADMIN``/``MANAGER``/``USER``;
  ``SYSTEM_ADMIN`` is always required (never stored, never waivable).
- ``session_idle_minutes``       — per-request inactivity window (5..10080);
  ``None`` = off.
- ``session_max_hours``          — hard session-lifetime cap (1..8760);
  ``None`` = the §8 30-day family cap stands.
- ``concurrent_session_limit``   — max live sign-in families per user
  (1..100); ``None`` = unlimited. Over the limit the **oldest-created**
  live family (not usage-based) is evicted (revoked + its bearer killed)
  at the next issuance.
- ``ip_allowlist``               — optional CIDR allowlist for session
  issuance/refresh (login + refresh); empty = unrestricted.
- ``patient_sharing``            — API-field name of the stored
  ``allow_patient_share`` key (the Phase-2 storage key is unchanged —
  existing tenant rows keep their value; the grants service keeps honoring
  it). ``false`` refuses patient-initiated shares.
- ``manager_scope``              — ``tenant`` (default) | ``assigned``.

Enforcement points:

- login/session issuance — ``POST /auth/login`` (MFA-required role step-up,
  IP allowlist) and ``issue_session`` (concurrent limit, window anchors);
- token verification — ``authenticate_session_token`` (idle / max-age via
  the per-family Redis window, ``sesspol:{fid}``);
- refresh — ``POST /auth/refresh`` (IP allowlist, idle, max-age against the
  family row);
- per request — the grants/access services re-read the policy knobs (the
  Phase-2 behavior, unchanged).

The window metadata and the family→jti binding live in the Redis jti store
(``app.core.token_store``) and degrade open on a Redis outage — the same
availability-first posture as revocation (the DB family rows remain the
authoritative record; the refresh path also enforces max-age from the row).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.enums import Role
from app.models.tenant_model import TenantModel

logger = logging.getLogger(__name__)

# --- vocabulary / bounds -----------------------------------------------------

#: Roles an admin may demand MFA for. ``SYSTEM_ADMIN`` is deliberately
#: absent — it is always required and can neither be stored nor waived.
POLICY_MFA_ROLES: tuple[str, ...] = (Role.ADMIN.value, Role.MANAGER.value, Role.USER.value)

SESSION_IDLE_MIN = 5
SESSION_IDLE_MAX = 10080  # 7 days, in minutes
SESSION_MAX_HOURS_MIN = 1
SESSION_MAX_HOURS_MAX = 8760  # 1 year
CONCURRENT_LIMIT_MIN = 1
CONCURRENT_LIMIT_MAX = 100
IP_ALLOWLIST_MAX = 64

DEFAULT_MANAGER_SCOPE = "tenant"
MANAGER_SCOPES = frozenset({"tenant", "assigned"})

#: The ``tenants.settings`` storage key behind the ``patient_sharing``
#: policy field (Phase-2 name kept so existing rows keep their value).
PATIENT_SHARING_STORAGE_KEY = "allow_patient_share"


def normalize_manager_scope(value: object) -> str:
    """Normalize the ``manager_scope`` knob (anything else → ``tenant``)."""
    return str(value) if value in MANAGER_SCOPES else DEFAULT_MANAGER_SCOPE


def normalize_patient_sharing(value: object) -> bool:
    """Normalize ``patient_sharing`` (default on; only falsey turns it off)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def normalize_mfa_required_roles(value: object) -> tuple[str, ...]:
    """Keep only the policy-eligible roles, deduped, in canonical order.

    Unknown names, ``SYSTEM_ADMIN`` and non-list shapes are dropped — reads
    are tolerant (a hand-edited settings row must never 500 a request);
    writes are validated upstream (pydantic → 422).
    """
    if not isinstance(value, (list, tuple, set)):
        return ()
    seen = {str(item) for item in value}
    return tuple(role for role in POLICY_MFA_ROLES if role in seen)


def normalize_bounded_int(value: object, lo: int, hi: int) -> int | None:
    """Coerce a bounded optional int; anything implausible is ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if lo <= number <= hi else None


def canonical_cidr(value: object) -> str | None:
    """The canonical form of one CIDR entry, or ``None`` when invalid."""
    import ipaddress

    try:
        return str(ipaddress.ip_network(str(value), strict=False))
    except (ValueError, TypeError):
        return None


def normalize_ip_allowlist(value: object) -> tuple[str, ...]:
    """Keep only valid CIDR entries (order preserved, capped)."""
    if not isinstance(value, (list, tuple)):
        return ()
    kept: list[str] = []
    for item in value:
        cidr = canonical_cidr(item)
        if cidr is not None and cidr not in kept:
            kept.append(cidr)
        if len(kept) >= IP_ALLOWLIST_MAX:
            break
    return tuple(kept)


# --- the normalized policy ---------------------------------------------------


@dataclass(frozen=True)
class SecurityPolicy:
    """The normalized tenant security policy (defaults for unset keys)."""

    tenant_id: UUID | None = None
    mfa_required_roles: tuple[str, ...] = ()
    session_idle_minutes: int | None = None
    session_max_hours: int | None = None
    concurrent_session_limit: int | None = None
    ip_allowlist: tuple[str, ...] = ()
    patient_sharing: bool = True
    manager_scope: str = DEFAULT_MANAGER_SCOPE

    def requires_mfa(self, role: Role | str) -> bool:
        """Whether this role must complete MFA before a session is issued.

        ``SYSTEM_ADMIN`` is always required — the role that reads every
        tenant cannot be exempted by tenant data.
        """
        resolved = role if isinstance(role, Role) else Role(role)
        if resolved is Role.SYSTEM_ADMIN:
            return True
        return resolved.value in self.mfa_required_roles

    def blocks_mfa_disable(self, role: Role | str) -> bool:
        """Whether the policy forbids self-disabling MFA for this role.

        Only the roles the tenant **explicitly listed** are pinned (an
        admin's demand is not self-cancellable, like ``mfa_enforced``).
        The ``SYSTEM_ADMIN`` constant gates login but not self-service:
        a bootstrap admin who disables an active secret is challenged into
        re-enrollment at the next login (the forced-enrollment flow), so
        the requirement never actually drops.
        """
        resolved = role if isinstance(role, Role) else Role(role)
        return resolved.value in self.mfa_required_roles

    def allows_ip(self, ip: str | None) -> bool:
        """Whether ``ip`` may open/refresh a session under this policy.

        An empty allowlist is unrestricted. A non-empty allowlist is
        fail-closed: an unparseable or missing client address is denied.
        """
        if not self.ip_allowlist:
            return True
        if not ip:
            return False
        import ipaddress

        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(
            address in ipaddress.ip_network(cidr, strict=False) for cidr in self.ip_allowlist
        )

    def to_dict(self) -> dict:
        """The API/audit shape (8C contract — ``patient_sharing`` field)."""
        return {
            "mfa_required_roles": list(self.mfa_required_roles),
            "system_admin_mfa_required": True,
            "session_idle_minutes": self.session_idle_minutes,
            "session_max_hours": self.session_max_hours,
            "concurrent_session_limit": self.concurrent_session_limit,
            "ip_allowlist": list(self.ip_allowlist),
            "patient_sharing": self.patient_sharing,
            "manager_scope": self.manager_scope,
        }


def normalize_security_policy(raw: object, *, tenant_id: UUID | None = None) -> SecurityPolicy:
    """Normalize a ``tenants.settings`` row into a :class:`SecurityPolicy`."""
    if not isinstance(raw, dict):
        raw = {}
    return SecurityPolicy(
        tenant_id=tenant_id,
        mfa_required_roles=normalize_mfa_required_roles(raw.get("mfa_required_roles")),
        session_idle_minutes=normalize_bounded_int(
            raw.get("session_idle_minutes"), SESSION_IDLE_MIN, SESSION_IDLE_MAX
        ),
        session_max_hours=normalize_bounded_int(
            raw.get("session_max_hours"), SESSION_MAX_HOURS_MIN, SESSION_MAX_HOURS_MAX
        ),
        concurrent_session_limit=normalize_bounded_int(
            raw.get("concurrent_session_limit"), CONCURRENT_LIMIT_MIN, CONCURRENT_LIMIT_MAX
        ),
        ip_allowlist=normalize_ip_allowlist(raw.get("ip_allowlist")),
        patient_sharing=normalize_patient_sharing(raw.get(PATIENT_SHARING_STORAGE_KEY)),
        manager_scope=normalize_manager_scope(raw.get("manager_scope")),
    )


async def tenant_security_policy(db: AsyncSession, tenant_id: UUID | str | None) -> SecurityPolicy:
    """Read one tenant's policy, normalized, per request (caller's session)."""
    tid = _coerce(tenant_id)
    if tid is None:
        return SecurityPolicy()
    result = await db.execute(select(TenantModel.settings).where(TenantModel.id == tid))
    return normalize_security_policy(result.scalar_one_or_none(), tenant_id=tid)


async def tenant_security_policy_for(tenant_id: UUID | str | None) -> SecurityPolicy:
    """Read one tenant's policy on the shared pool (no request session).

    The verification/issuance paths (``authenticate_session_token``,
    ``issue_session``, the auth endpoints) run without a request-scoped
    session, so they use this helper — one small ``settings`` read per
    call, per the no-token-caching rule. An unresolvable tenant id (or no
    database) answers the defaults — a policy must never be the reason a
    request cannot be verified.
    """
    tid = _coerce(tenant_id)
    if tid is None or not DATABASE_AVAILABLE:
        return SecurityPolicy()
    async with AsyncSessionLocal() as session:
        return await tenant_security_policy(session, tid)


def _coerce(value: UUID | str | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# --- writes ------------------------------------------------------------------

#: Validated patch key → the ``tenants.settings`` storage key. The two
#: Phase-2 knobs keep their stored names (``patient_sharing`` is the API
#: field of ``allow_patient_share``); everything else stores as-is.
_SETTING_KEYS: dict[str, str] = {
    "mfa_required_roles": "mfa_required_roles",
    "session_idle_minutes": "session_idle_minutes",
    "session_max_hours": "session_max_hours",
    "concurrent_session_limit": "concurrent_session_limit",
    "ip_allowlist": "ip_allowlist",
    "patient_sharing": PATIENT_SHARING_STORAGE_KEY,
    "manager_scope": "manager_scope",
}


def policy_patch_to_settings(changes: dict) -> dict:
    """Map a validated API patch onto the ``tenants.settings`` storage keys,
    normalizing each value into its stored form."""
    import ipaddress

    stored: dict = {}
    for field, value in changes.items():
        key = _SETTING_KEYS.get(field)
        if key is None:
            continue
        if field == "mfa_required_roles":
            stored[key] = list(normalize_mfa_required_roles(value))
        elif field in ("session_idle_minutes", "session_max_hours", "concurrent_session_limit"):
            stored[key] = value
        elif field == "ip_allowlist":
            stored[key] = [str(ipaddress.ip_network(v, strict=False)) for v in (value or [])]
        elif field == "patient_sharing":
            stored[key] = bool(value)
        elif field == "manager_scope":
            stored[key] = normalize_manager_scope(value)
    return stored


async def update_security_policy(
    db: AsyncSession, tenant: TenantModel, changes: dict
) -> tuple[dict, dict]:
    """Apply a validated patch to one tenant's settings; return the
    (before, after) changed-key dicts for the audit diff.

    Only the provided keys are touched; unknown settings keys are preserved.
    The caller owns the commit and the audit row.
    """
    stored = policy_patch_to_settings(changes)
    before = {key: (tenant.settings or {}).get(key) for key in stored}
    settings = dict(tenant.settings or {})
    settings.update(stored)
    tenant.settings = settings
    flag_modified(tenant, "settings")
    after = {key: settings.get(key) for key in stored}
    return before, after


# --- per-request window enforcement (Redis, degrade-open) ---------------------
# The idle/max-age window and the family→jti binding are Redis facts owned by
# ``app.core.token_store``; this module turns them into allow/deny answers.

#: Typed denial details (8C contract — stable strings, not free text).
DENY_IDLE = "session_idle_expired"
DENY_MAX_AGE = "session_max_age_expired"
DENY_IP = "ip_not_allowed"


def request_client_ip(request: object) -> str | None:
    """The request's client address (``request.client.host``).

    Behind a reverse proxy this is the proxy's address unless the ASGI
    server is started with ``--proxy-headers`` (uvicorn) and a trusted
    ``FORWARDED_ALLOW_IPS`` — deployments enforcing an IP allowlist must
    run with proxy headers enabled so the address is the real client.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or None


async def check_session_window(policy: SecurityPolicy, family_id: str | None) -> str | None:
    """The session-window verdict for one request: ``None`` = allow, else a
    typed denial reason (:data:`DENY_IDLE` / :data:`DENY_MAX_AGE`).

    No family id or an inactive policy window allows. A missing Redis entry
    (fresh token, Redis flush, policy enabled later) re-anchors the window
    at *now* — the same availability-first degrade-open posture as the jti
    store; the refresh path additionally enforces max-age from the
    authoritative family row.
    """
    from app.core import token_store

    if not family_id or (policy.session_idle_minutes is None and policy.session_max_hours is None):
        return None
    verdict = await token_store.session_window_check(
        family_id,
        idle_seconds=policy.session_idle_minutes * 60 if policy.session_idle_minutes else None,
        max_seconds=policy.session_max_hours * 3600 if policy.session_max_hours else None,
    )
    if verdict == "idle":
        return DENY_IDLE
    if verdict == "max_age":
        return DENY_MAX_AGE
    return None


async def anchor_session_window(policy: SecurityPolicy, family_id: str | None) -> None:
    """Anchor the window for a freshly minted family (issuance)."""
    from app.core import token_store

    if not family_id:
        return
    await token_store.session_window_anchor(family_id)

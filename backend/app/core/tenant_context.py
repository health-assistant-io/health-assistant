"""Row-level security request context (ADR 0054 D4, program phase 11).

The database enforces tenant isolation a **second time**, below the
application layer: every tenant-scoped table carries row-level security
policies that read transaction-local GUCs planted on each connection:

=================  =====================================================
GUC                Meaning
=================  =====================================================
``app.tenant_id``  The authenticated principal's tenant (UUID string).
``app.system``     ``on`` only for the system actor: SYSTEM_ADMIN
                   principals, the auth layer's pre-auth bootstrap
                   lookups (login/refresh/register/OAuth/webhook),
                   service jobs (Celery tasks), and boot-time database
                   work (``app.core.boot`` — "boot:<step>" actor names).
                   Never derived from request data.
``app.patient_id`` The facade principal's patient binding. Reserved —
                   no policy consumes it yet (Phase 13 scoped
                   principals); planted so the inventory is complete.
``app.actor``      Observability only (principal id / task name) —
                   visible in ``pg_stat_activity``; never read by
                   policies.
=================  =====================================================

Policies fail closed: ``tenant_id = NULLIF(current_setting('app.tenant_id',
true), '')::uuid`` — a missing or reset GUC reads as empty string/NULL and
yields NULL, never a match; ``COALESCE(current_setting('app.system',
true)::boolean-parsed, false)`` never opens the system door implicitly.

Wiring: application sessions install a ``after_begin`` session hook
(:func:`install_session_hook`) that applies the current contextvars to the
connection at the start of **every** transaction via
``set_config(..., is_local => true)``. The values are transaction-scoped:
a pooled connection never carries a context past commit/rollback, and each
transaction re-plants (thereby resetting) **all** GUCs — an empty string
means "unset" for the policies, so pool bleed and stale contexts are
structurally impossible.

Rules (ADR 0054 D4):

- ``app.system`` is set only by the auth layer and by service jobs — never
  from request data.
- RLS is the **second wall**; application code keeps its ``tenant_id``
  filters and ownership predicates unchanged.
- ``SET LOCAL``/``set_config`` runs at transaction begin on the engine, not
  per request — pooled async connections make request-scoped GUCs
  untestable (ADR 0054 D4, alternatives).
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

TENANT_GUC = "app.tenant_id"
SYSTEM_GUC = "app.system"
PATIENT_GUC = "app.patient_id"
ACTOR_GUC = "app.actor"

_tenant_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "rls_tenant_id", default=None
)
_system: contextvars.ContextVar[bool] = contextvars.ContextVar("rls_system", default=False)
_patient_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "rls_patient_id", default=None
)
_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar("rls_actor", default=None)

_SET_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.system', :system, true), "
    "set_config('app.patient_id', :patient_id, true), "
    "set_config('app.actor', :actor, true)"
)


def _snapshot() -> dict[str, UUID | bool | str | None]:
    """The current context values (for scoped restoration)."""
    return {
        "tenant_id": _tenant_id.get(),
        "system": _system.get(),
        "patient_id": _patient_id.get(),
        "actor": _actor.get(),
    }


def _restore(snapshot: dict) -> None:
    _tenant_id.set(snapshot["tenant_id"])
    _system.set(snapshot["system"])
    _patient_id.set(snapshot["patient_id"])
    _actor.set(snapshot["actor"])


def _apply(
    *,
    tenant_id: UUID | None,
    system: bool,
    patient_id: UUID | None,
    actor: str | None,
) -> None:
    _tenant_id.set(tenant_id)
    _system.set(system)
    _patient_id.set(patient_id)
    _actor.set(actor)


def set_principal(principal) -> None:
    """Plant the context for an authenticated principal (the auth layer).

    ``principal`` is a :class:`~app.schemas.user.TokenData` — session or
    api kind. The system flag follows the ROLE (SYSTEM_ADMIN is the system
    actor and keeps its operator reach — today's documented behavior; the
    role drives the flag, switching does not). A scoped machine credential
    (``pat_id`` / ``integration_principal_id``) is **never** the system actor,
    whatever its owner's role: a bearer secret stays inside its tenant (ADR
    0054 Phase 6/13). A switched SYSTEM_ADMIN
    therefore reads rows everywhere — the switch scopes *authorization*
    (route policies, tenant-admin checks), never the RLS system actor.

    What the planted tenant GUC carries for a switched principal is
    deliberate and pinned by the RLS suite: the **home** tenant
    (``original_tenant_id``), never the switch target. Under the system
    flag no policy needs the tenant, so this is identity-honesty, not
    enforcement: the GUC says who the principal is (``pg_stat_activity``
    observability, ``current_tenant_id`` consumers), while the operation
    tenant lives on the token claim (``TokenData.tenant_id`` = switch
    target) and the home-tenant user row stays resolvable by the
    verifier's own lookups.
    """
    role = getattr(principal, "role", "") or ""
    tenant_id = getattr(principal, "tenant_id", None)
    if getattr(principal, "switched", False):
        tenant_id = getattr(principal, "original_tenant_id", None) or tenant_id
    actor = getattr(principal, "user_id", None) or getattr(principal, "client_id", None)
    scoped_token = isinstance(getattr(principal, "pat_id", None), str) or isinstance(
        getattr(principal, "integration_principal_id", None), str
    )
    _apply(
        tenant_id=tenant_id,
        system=(role == "SYSTEM_ADMIN") and not scoped_token,
        patient_id=getattr(principal, "bound_patient_id", None),
        actor=str(actor) if actor is not None else None,
    )


def set_tenant(tenant_id: UUID | str | None, *, actor: str | None = None) -> None:
    """Plant a tenant-only context (token-derived; system stays closed).

    Used by token-principal surfaces that are not session verifications —
    presigned download/preview tokens plant the token's tenant (the claim
    is signature-verified before it is trusted).
    """
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    _apply(tenant_id=tenant_id, system=False, patient_id=None, actor=actor)


def clear() -> None:
    """Reset the context to the closed state (no tenant, no system)."""
    _apply(tenant_id=None, system=False, patient_id=None, actor=None)


@contextmanager
def system_scope(actor: str | None = None):
    """Run a block as the system actor (no tenant constraint).

    The auth layer's bootstrap lookups (email/user/client/integration
    resolution before any principal exists) and service jobs run inside
    this scope; the previous context is restored on exit, so the scope
    never leaks past the block — "no ambient system" (ADR 0054 Phase 5).
    """
    previous = _snapshot()
    _apply(tenant_id=None, system=True, patient_id=None, actor=actor)
    try:
        yield
    finally:
        _restore(previous)


@contextmanager
def tenant_scope(tenant_id: UUID | None, *, actor: str | None = None):
    """Run a block under one tenant's context (system stays closed)."""
    previous = _snapshot()
    _apply(tenant_id=tenant_id, system=False, patient_id=None, actor=actor)
    try:
        yield
    finally:
        _restore(previous)


def apply_to_connection(connection) -> None:
    """Plant all four GUCs transaction-locally on ``connection``.

    Called by the ``after_begin`` hook at every transaction begin and by
    the audit lane when an event's chain context must override the
    ambient one. Every call writes **all** GUCs — empty string means
    "unset" to the policies — so a connection can never inherit a value
    from anything but the transaction's own context.
    """
    connection.execute(
        _SET_CONTEXT_SQL,
        {
            "tenant_id": str(_tenant_id.get()) if _tenant_id.get() else "",
            "system": "on" if _system.get() else "off",
            "patient_id": str(_patient_id.get()) if _patient_id.get() else "",
            "actor": _actor.get() or "",
        },
    ).fetchone()


def _after_begin(session, transaction, connection) -> None:
    """Session ``after_begin`` hook — see :func:`install_session_hook`."""
    apply_to_connection(connection)


def install_session_hook(sessionmaker: async_sessionmaker) -> None:
    """Wire the context hook onto a session factory's sync session class.

    Each factory gets a **distinct** ``sync_session_class`` so the hook
    applies exactly to the application's sessions (main + audit lanes;
    workers register their own factory the same way) and never to foreign
    Session users (langgraph's checkpointer).
    """
    event.listen(sessionmaker.class_.sync_session_class, "after_begin", _after_begin)


async def plant_on_session(session) -> None:
    """Re-plant the ambient context on a session's **already open** transaction.

    The ``after_begin`` hook plants the GUCs only when a transaction begins.
    A scoped elevation that starts *inside* an open transaction must call
    this itself after entering the scope (and again after leaving it), or the
    next statement still reads the GUCs planted at begin. The SSO domain-claim
    scan is the one caller: the admin request's transaction is already open
    when the cross-tenant claim check runs.

    ``set_config(..., is_local => true)`` keeps the switch transaction-scoped,
    so the caller's ``finally`` restoration is belt-and-braces — a rolled-back
    transaction can never leak the widened context to the pool.
    """
    connection = await session.connection()
    await connection.run_sync(apply_to_connection)


def current_tenant_id() -> UUID | None:
    """The context's tenant (``None`` = unset)."""
    return _tenant_id.get()


def system_enabled() -> bool:
    """Whether the context is the system actor."""
    return _system.get()


def current_patient_id() -> UUID | None:
    """The context's patient binding (facade principals; ``None`` = unset)."""
    return _patient_id.get()

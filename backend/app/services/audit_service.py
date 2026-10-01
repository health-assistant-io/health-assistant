"""Audit-stream service — the single chokepoint for audit events.

identity-auth §17 (Class S): ``audit_events`` (actor, action, resource,
tenant, outcome, timestamp) must record **record reads, record writes,
and every admin action**. Everything flows through :func:`log_audit_action`
(extended in plan 16 H2 with the ``outcome`` column), which:

- Opens its own short-lived session (so the audit row is committed even if the
  caller later rolls back — the fact that the action *happened* is itself the
  audit fact of interest).
- Never raises — an audit-logging failure must not break the user's request.
  Failures are logged at WARNING with the traceback (never silently
  swallowed).
- Accepts ``old_value`` / ``new_value`` as dicts and stores them as JSONB for
  full diff capability.
- Uses ``tenant_id = None`` for system-level actions so global writes still
  get a trail.

Outcome semantics (§5 additions, H2):

- ``ok`` — the default; the action/read succeeded.
- ``denied`` — access refused: hidden-404s, 403s, failed logins, refresh
  reuse detection.
- ``error`` — the action was attempted but failed for another reason.

For record **reads** (sensitive clinical GET paths), use
:func:`audit_read` — an endpoint decorator that records one event per
read with the outcome derived from the response (2xx → ok, 401/403/404 →
denied, anything else → error). It awaits the write inline (one insert)
but can never block or fail the response because ``log_audit_action``
never raises.
"""

import functools
import logging
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.core.errors import AuthorizationError, NotFoundError
from app.models.audit_model import AuditEvent

logger = logging.getLogger(__name__)

#: Normative outcome values (identity-auth §17 — outcome of the action).
OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_ERROR = "error"

#: HTTP statuses that count as access *denial* (hidden-404 included — the
#: health product deliberately hides cross-tenant existence, §7).
_DENIED_STATUSES = frozenset({401, 403, 404})


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def log_audit_action(
    *,
    tenant_id: UUID | None,
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    outcome: str = OUTCOME_OK,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    """Persist an ``AuditEvent`` row.

    Parameters mirror the ``AuditEvent`` columns. ``old_value``/``new_value``
    are stored as JSONB; pass ``to_dict()`` snapshots for full diffs.
    ``outcome`` is one of ``ok``/``denied``/``error`` (§17). The function is
    best-effort: any failure is logged at WARNING and swallowed so it can
    never break the calling request.
    """
    if not DATABASE_AVAILABLE:
        return

    try:
        entry = AuditEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action[:100],
            resource_type=resource_type[:100],
            resource_id=_coerce_uuid(resource_id),
            outcome=(outcome or OUTCOME_OK)[:20],
            old_value=old_value,
            new_value=new_value,
        )
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
    except Exception as e:
        logger.warning(
            "Failed to write AuditEvent (action=%s resource=%s/%s outcome=%s): %s",
            action,
            resource_type,
            resource_id,
            outcome,
            e,
            exc_info=True,
        )


def _outcome_for(exc: Exception) -> tuple[str, int | None]:
    """Map a handler exception to (outcome, http_status).

    Domain errors (``NotFoundError``/``AuthorizationError`` — the
    ``check_*_access`` helpers) and HTTP 401/403/404 map to ``denied``;
    everything else maps to ``error``.
    """
    if isinstance(exc, HTTPException):
        if exc.status_code in _DENIED_STATUSES:
            return OUTCOME_DENIED, exc.status_code
        return OUTCOME_ERROR, exc.status_code
    if isinstance(exc, (NotFoundError, AuthorizationError)):
        status = 404 if isinstance(exc, NotFoundError) else 403
        return OUTCOME_DENIED, status
    return OUTCOME_ERROR, None


def audit_read(resource_type: str, *, id_param: str | None = None):
    """Decorator for sensitive clinical GET endpoints (§17 record reads).

    Records one ``AuditEvent`` per read: ``action = "read_<resource>"``,
    the record id from the ``id_param`` path parameter (or the first
    ``*_id`` kwarg), the caller as actor, and the outcome derived from
    what the handler did:

    - returned normally → ``ok``
    - raised 401/403/404 (or the domain ``NotFoundError`` /
      ``AuthorizationError`` the ``check_*_access`` helpers raise) →
      ``denied``, with the HTTP status kept in ``new_value``
    - any other failure → ``error``

    The audit write is awaited inline but can never block or fail the
    response — ``log_audit_action`` never raises and logs its own
    failures. Apply **before** registering the route (FastAPI resolves
    the dependency-injection signature through ``functools.wraps``).
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            user = kwargs.get("current_user")
            tenant_id = _coerce_uuid(getattr(user, "tenant_id", None))
            user_id = _coerce_uuid(getattr(user, "user_id", None))
            resource_id = (
                kwargs.get(id_param)
                if id_param is not None
                else next((v for k, v in kwargs.items() if k.endswith("_id")), None)
            )
            action = "read_" + re.sub(r"(?<!^)(?=[A-Z])", "_", resource_type).lower()

            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                outcome, http_status = _outcome_for(exc)
                await log_audit_action(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=_coerce_uuid(resource_id),
                    outcome=outcome,
                    new_value=(
                        {"http_status": http_status}
                        if http_status is not None
                        else {"error": type(exc).__name__}
                    ),
                )
                raise
            await log_audit_action(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=_coerce_uuid(resource_id),
                outcome=OUTCOME_OK,
            )
            return result

        return wrapper

    return decorator

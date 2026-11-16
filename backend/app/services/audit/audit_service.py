"""Audit-stream service — the single chokepoint for audit events.

identity-auth §17 (Class S): ``audit_events`` (actor, action, resource,
tenant, outcome, timestamp) must record **record reads, record writes,
and every admin action**. Everything flows through :func:`log_audit_action`,
which:

- Opens its own short-lived session on the **audit lane pool**
  (:data:`app.core.database.AuditSessionLocal` — a dedicated small pool, so
  audit writes never borrow a request connection) and commits the row with
  its hash-chain append before returning. The caller's later rollback does
  not un-write the fact that the action was attempted.
- Extends the per-tenant hash chain (ADR 0054 D3 —
  :mod:`app.services.audit.audit_chain`): the row carries
  ``chain_key``/``seq``/``prev_hash``/``entry_hash``; concurrent appends of
  one chain serialize on the chain's head row.
- Redacts both diff payloads through
  :mod:`app.services.audit.redaction` before they are stored — PHI never
  reaches ``audit_events``.
- **Fails closed** for the security-critical surfaces (writes, admin
  actions, grants, auth events, authz denials, PHI reads): pass
  ``required=True`` and a failed audit write raises :class:`AuditWriteError`
  instead of returning — the action is refused rather than succeeding
  unrecorded. A failed *required* write is logged at CRITICAL before the
  error propagates.

  "Refused rather than succeeding unrecorded" is exact **only** when the
  audit row rides the caller's transaction: pass ``session=<the caller's
  AsyncSession>`` and the row commits (or rolls back) together with the
  action — the sanctioned shape for security-critical admin/policy writes
  (tenant security policy, tenant/user administration). Without a
  ``session``, the write commits on its own lane *after* the action's
  transaction: a failure still raises :class:`AuditWriteError` (the caller
  answers 500 and the CRITICAL alarm says the action landed unrecorded),
  but the action itself is already durable — so a callsite whose action
  and audit row must live or die together MUST pass ``session=``.
  Record **reads** keep their dedicated fail-closed lane (§17 — the read
  is answered only after the proof-of-read is durable).

Failure policy (S3 / ADR 0054 D3) — the only sanctioned **best-effort**
lane is the post-rollback recording inside :func:`audit_read` for denied /
error outcomes (the original exception must keep its status; the record is
a denial *record*, the denial itself is already enforced). That lane logs a
CRITICAL ``audit_alarm`` on failure — never a silent drop. Every other
callsite in the app passes ``required=True``; there are no remaining
best-effort telemetry callers.

Outcome semantics (§5 additions, H2):

- ``ok`` — the default; the action/read succeeded.
- ``denied`` — access refused: hidden-404s, 403s, failed logins, refresh
  reuse detection, authz-gate denials.
- ``error`` — the action was attempted but failed for another reason.

For record **reads** (sensitive clinical GET paths), use
:func:`audit_read` — an endpoint decorator that records one event per
read with the outcome derived from the response (2xx → ok, 401/403/404 →
denied, anything else → error). The allowed outcome is written
``required=True``: a PHI read that cannot be audited does not answer
(D3 — PHI reads fail closed). Denied/error outcomes are recorded after
the handler raised, on the dedicated lane, best-effort with an alarm.
"""

import functools
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context
from app.core.database import AUDIT_DATABASE_AVAILABLE, AuditSessionLocal
from app.core.errors import AuthorizationError, NotFoundError
from app.models.audit_model import AuditEvent
from app.services.audit.audit_chain import append as _chain_append
from app.services.audit.audit_chain import chain_key_for
from app.services.audit.redaction import redact_diffs

logger = logging.getLogger(__name__)

#: Normative outcome values (identity-auth §17 — outcome of the action).
OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_ERROR = "error"

#: HTTP statuses that count as access *denial* (hidden-404 included — the
#: health product deliberately hides cross-tenant existence, §7).
_DENIED_STATUSES = frozenset({401, 403, 404})


class AuditWriteError(RuntimeError):
    """A required audit write could not be recorded — the action fails.

    Raised only on ``required=True`` writes (the fail-closed policy, S3 /
    ADR 0054 D3): an unrecorded write/admin/grant/auth action is refused.
    Mapped to a generic 500 by the global exception handler; the alarm log
    carries the cause.
    """


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


_CHAIN_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), set_config('app.system', :system, true)"
)


def _current_context_snapshot() -> dict[str, UUID | bool]:
    """The ambient RLS context, for post-append restoration."""
    return {
        "tenant_id": tenant_context.current_tenant_id(),
        "system": tenant_context.system_enabled(),
    }


async def _apply_chain_context(target: AsyncSession, context: dict[str, UUID | bool]) -> None:
    """Plant the event's chain context on the target session's transaction."""
    await target.execute(
        _CHAIN_CONTEXT_SQL,
        {
            "tenant_id": str(context["tenant_id"]) if context["tenant_id"] else "",
            "system": "on" if context["system"] else "off",
        },
    )


async def _restore_chain_context(target: AsyncSession, previous: dict[str, UUID | bool]) -> None:
    """Restore the caller's context after a same-transaction append.

    A failed append aborts the transaction (SET LOCAL values die with it),
    so a failed restore is swallowed — the caller's rollback cleans up.
    """
    try:
        await _apply_chain_context(target, previous)
    except Exception:
        logger.debug("audit context restore skipped — transaction no longer live")


async def log_audit_action(
    *,
    tenant_id: UUID | None,
    user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    outcome: str = OUTCOME_OK,
    old_value: dict[str, Any] | None = None,  # gate-allow: *, audit diff
    new_value: dict[str, Any] | None = None,
    required: bool = False,
    session: AsyncSession | None = None,
) -> None:
    """Persist one redacted, hash-chained ``AuditEvent`` row.

    Parameters mirror the ``AuditEvent`` columns. The two diff columns are
    redacted (PHI map) and stored as JSONB; pass ``to_dict()`` snapshots
    of *persisted model state* for full diffs — raw request payloads can
    carry unknown keys past the redaction map. ``outcome`` is one of
    ``ok``/``denied``/``error`` (§17).

    ``session`` (S1) — pass the caller's ``AsyncSession`` to append the
    audit row **inside the caller's transaction**: the row commits (or
    rolls back) together with the action, so a failed write refuses the
    action exactly as ``required=True`` promises. The caller owns the
    commit; nothing is written on the audit lane. Without ``session``, the
    row is written and committed on the dedicated audit lane before
    returning — the post-rollback denial lane and the record-read path
    keep that shape.

    ``required=True`` is the fail-closed policy: a failed write raises
    :class:`AuditWriteError` so the calling action cannot succeed
    unrecorded. The default (``False``) is the sanctioned post-rollback
    denial lane only — its failures alarm at CRITICAL and are swallowed so
    the original exception keeps its status.
    """
    if (not AUDIT_DATABASE_AVAILABLE or AuditSessionLocal is None) and session is None:
        if required:
            raise AuditWriteError(f"audit storage unavailable — refusing unrecorded {action}")
        logger.critical(
            "audit_alarm: dropped best-effort AuditEvent "
            "(action=%s resource=%s/%s outcome=%s): audit lane unavailable",
            action,
            resource_type,
            resource_id,
            outcome,
        )
        return

    old_value, new_value = redact_diffs(old_value, new_value)  # gate-allow: *, audit diff
    entry = AuditEvent(
        tenant_id=_coerce_uuid(tenant_id),
        user_id=_coerce_uuid(user_id),
        action=action[:100],
        resource_type=resource_type[:100],
        resource_id=_coerce_uuid(resource_id),
        outcome=(outcome or OUTCOME_OK)[:20],
        old_value=old_value,  # gate-allow: *, audit diff
        new_value=new_value,
        created_at=datetime.now(UTC),
        chain_key=chain_key_for(_coerce_uuid(tenant_id)),
    )
    event_context = {
        "tenant_id": _coerce_uuid(tenant_id),
        "system": _coerce_uuid(tenant_id) is None,
    }
    if session is not None:
        # Same-transaction shape (S1): append on the caller's session; the
        # caller's commit/rollback owns the outcome. A failed append fails
        # the action the same way a dedicated-lane failure would. The
        # event's chain context is planted for the append and restored
        # afterwards so the caller's transaction keeps its own context.
        previous = _current_context_snapshot()
        await _apply_chain_context(session, event_context)
        try:
            await _chain_append(session, entry)
        except Exception as e:
            logger.critical(
                "audit_alarm: same-transaction AuditEvent failed "
                "(action=%s resource=%s/%s outcome=%s): %s",
                action,
                resource_type,
                resource_id,
                outcome,
                e,
                exc_info=True,
            )
            if required:
                raise AuditWriteError(f"required audit write failed for {action}") from e
        finally:
            await _restore_chain_context(session, previous)
        return
    try:
        async with AuditSessionLocal() as audit_session, audit_session.begin():
            # The audit lane plants the event's own chain context: a
            # tenant chain row needs the matching tenant GUC, the system
            # chain the system flag (ADR 0054 Phase 5) — regardless of
            # what ambient context the caller carried.
            await _apply_chain_context(audit_session, event_context)
            await _chain_append(audit_session, entry)
    except Exception as e:
        if required:
            logger.critical(
                "audit_alarm: required AuditEvent failed (action=%s resource=%s/%s outcome=%s): %s",
                action,
                resource_type,
                resource_id,
                outcome,
                e,
                exc_info=True,
            )
            raise AuditWriteError(f"required audit write failed for {action}") from e
        logger.critical(
            "audit_alarm: best-effort AuditEvent dropped (action=%s resource=%s/%s outcome=%s): %s",
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

    - returned normally → ``ok``, written **fail-closed**
      (``required=True``): if the audit write cannot be recorded the read
      answers 500 instead of PHI (D3 — PHI reads fail closed).
    - raised 401/403/404 (or the domain ``NotFoundError`` /
      ``AuthorizationError`` the ``check_*_access`` helpers raise) →
      ``denied``, with the HTTP status kept in ``new_value``; recorded
      after the exception on the dedicated lane, best-effort with a
      CRITICAL alarm on failure (the original status propagates).
    - any other failure → ``error``, same lane.

    Denial ownership (S7): this decorator owns the *handler-level*
    outcomes only. Gate-level denials (401/403 raised by the central
    authz gate before the handler runs) are owned by
    ``enforce_route_policy`` — the wrapper below never executes for them,
    so no request is double-recorded. Apply **before** registering the
    route (FastAPI resolves the dependency-injection signature through
    ``functools.wraps``).
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
                required=True,
            )
            return result

        return wrapper

    return decorator

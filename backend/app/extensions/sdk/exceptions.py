"""Exception hierarchy for the Integrations SDK.

These exceptions are the contract between integration providers and the
platform engine. The sync worker (``app/workers/tasks.py`` +
``app/services/integration_sync_service.py``) catches them and routes
the sync outcome accordingly:

* ``IntegrationAuthError``  → integration status flips to ``ERROR``;
  the UI surfaces a "reconnect" prompt.
* ``IntegrationRateLimitError`` → sync skipped gracefully; status
  stays ``ACTIVE``. If the exception carries ``retry_after_seconds``,
  the worker writes a Redis-backed cooldown key so the next beat
  skips this integration until the cooldown expires (defending
  against the worker hammering the upstream every 60 s while the
  window is still closed).
* ``IntegrationDataError``   → sync failed; logged for the user.
"""

from __future__ import annotations


class IntegrationError(Exception):
    """Base exception for all Integration SDK errors."""


class IntegrationAuthError(IntegrationError):
    """Raised when an integration fails due to invalid/expired credentials.

    Catching this should trigger a re-authentication prompt for the user.
    """


class IntegrationRateLimitError(IntegrationError):
    """Raised when an API rate limit is exhausted and retries are spent.

    Carries an optional ``retry_after_seconds`` hint — sourced from the
    upstream's ``Retry-After`` header when available
    (:func:`integrations.sdk.http._retry_request` captures the last
    seen value before raising). When set, the sync worker writes a
    Redis cooldown key ``sync_cooldown:{integration_id}`` with the
    given TTL (clamped to a sane window) so subsequent beats skip the
    integration instead of re-hitting the upstream every 60 seconds.

    Backwards compatible: ``raise IntegrationRateLimitError("msg")``
    (the pre-2.1 call shape) still works and leaves the attribute at
    ``None`` (no cooldown, falls back to the per-instance
    ``sync_interval`` throttle).
    """

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class IntegrationDataError(IntegrationError):
    """Raised when the third-party API returns malformed or unexpected data."""


class IntegrationConfigError(IntegrationError):
    """Raised when an integration's configuration is invalid or incomplete.

    Distinct from :class:`IntegrationAuthError` (invalid/expired credentials →
    "reconnect") and :class:`IntegrationDataError` (bad upstream payload →
    "sync failed"). Use this when the user needs to **reconfigure** the
    integration: a malformed OAuth ``redirect_uri``, a SMART discovery that
    didn't return required endpoints, a DCR rejection, or a secret key that
    can't decrypt a stored value.

    The sync worker treats this like :class:`IntegrationDataError` (sync
    failed, logged) by default; callers that want a distinct UI surface
    ("Fix configuration" vs "Reconnect") can branch on the type.
    """


class BridgeError(ValueError):
    """A client-facing health-assistant-bridge error with an HTTP identity.

    Raised by the bridge provider's guard sites and mapped by the
    integration API proxy to
    ``HTTPException(status_code, detail={"code": ..., "message": ...})`` —
    mobile clients branch on :attr:`code` (a stable, closed vocabulary)
    instead of string-matching messages. Subclasses :class:`ValueError` so
    the proxy's legacy ``ValueError → 400`` path (and any older caller)
    keeps working.

    The code vocabulary (closed — adding a code requires updating the
    Errors table in ``integrations/health_assistant_bridge/docs/
    api-reference.md`` in the same commit):

    * ``not_found``     (404) — foreign or missing session/exam/document/
      clinical-event/… rows
    * ``turn_conflict`` (409) — the session's single-flight turn lock is held
    * ``hitl_conflict`` (409) — the HITL proposal was already resolved
    * ``validation``    (400) — malformed input / failed guard; also the
      default for a bare :class:`ValueError` reaching the proxy
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code

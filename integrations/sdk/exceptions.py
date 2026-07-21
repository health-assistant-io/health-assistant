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

from typing import Optional


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
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class IntegrationDataError(IntegrationError):
    """Raised when the third-party API returns malformed or unexpected data."""

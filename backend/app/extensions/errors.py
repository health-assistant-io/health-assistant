"""The charter typed-error model (ADR-0040 clause 7 / D4) + the bridge's
HTTP-identity vocabulary.

``ExtensionError`` is the ONE error family extensions and the engine
exchange: a closed 15-code enum (``net-denied``, ``auth``,
``rate-limited``, …) + capped ``detail`` + a closed ``retryable``
sub-enum, optionally carrying ``retry_after_ms`` (the surfaced 429
payload). The per-code ``retryable`` defaults come from the vendored
family runtime (:mod:`app.extensions.polite_http`) — one source, never
re-declared here. Raw exception strings never cross an extension
boundary; ``to_dict()`` is the wire shape for logs, status rows and
result envelopes.

This module REPLACES the historical ``IntegrationError`` class tree
(Auth/RateLimit/Data/Config) — a one-time destructive re-authoring
(D18), no aliases, no shims. The re-authoring map (the D4 note, recorded
once):

==========================  =======================================
old class                   charter code
==========================  =======================================
IntegrationAuthError        ``auth``
IntegrationRateLimitError   ``rate-limited`` (retry_after_seconds →
                            retry_after_ms)
IntegrationDataError        split by cause: ``net-denied`` (the SSRF
                            guard), ``protocol`` (malformed upstream
                            payload), ``provider`` (upstream 5xx),
                            ``unreachable`` (transport-level failure)
IntegrationConfigError      ``invalid`` (bad configuration shape) or
                            ``unsupported`` (missing capability/host)
IntegrationError (generic)  ``not-ready`` (prerequisites missing) or
                            ``io``
SSRFBlockedError            ``net-denied`` — non-retryable config
                            class: logged, the instance stays ACTIVE
                            (the historical SSRFBlock semantics,
                            preserved exactly)
==========================  =======================================

``BridgeError`` stays: it is the inbound two-way API's HTTP-identity
vocabulary (status_code + closed client-facing code), which mobile
clients branch on — inbound auth/surface mechanics are host policy per
the charter's bridge-kind note, not the sync-engine error model.
"""

from __future__ import annotations

from typing import Any

from app.extensions.polite_http import (
    DEFAULT_RETRYABLE,
    ERROR_CODES,
    RETRYABLE_VOCABULARY,
)

__all__ = [
    "DEFAULT_RETRYABLE",
    "ERROR_CODES",
    "RETRYABLE_VOCABULARY",
    "BridgeError",
    "ExtensionError",
]


class ExtensionError(Exception):
    """A typed charter error — one of the 15 closed codes + capped detail.

    ``retryable`` defaults per code from the charter and may not leave the
    closed sub-enum; ``retry_after_ms`` carries the 429/Retry-After
    payload when known; ``status`` keeps the upstream HTTP status when one
    was received.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: str | None = None,
        retry_after_ms: int | None = None,
        status: int | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown charter error code: {code!r}")
        self.code = code
        self.detail = detail[:500]
        self.retryable = retryable or DEFAULT_RETRYABLE[code]
        if self.retryable not in RETRYABLE_VOCABULARY:
            raise ValueError(f"retryable outside the closed sub-enum: {self.retryable!r}")
        self.retry_after_ms = retry_after_ms
        self.status = status
        super().__init__(f"{code}: {self.detail}")

    @classmethod
    def from_polite(cls, exc: Any) -> ExtensionError:
        """Adopt a :class:`polite_http.PoliteHttpError` verbatim (the
        vendored runtime already speaks the charter model)."""
        return cls(
            exc.code,
            exc.detail,
            retryable=exc.retryable,
            retry_after_ms=getattr(exc, "retry_after_ms", None),
            status=getattr(exc, "status", None),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "detail": self.detail,
            "retryable": self.retryable,
        }
        if self.retry_after_ms is not None:
            payload["retry_after_ms"] = self.retry_after_ms
        return payload


class BridgeError(ValueError):
    """A client-facing health-assistant-bridge error with an HTTP identity.

    Raised by the bridge extension's guard sites and mapped by the
    integration API proxy to
    ``HTTPException(status_code, detail={"code": ..., "message": ...})`` —
    mobile clients branch on :attr:`code` (a stable, closed vocabulary)
    instead of string-matching messages.

    The code vocabulary (closed — adding a code requires updating the
    Errors table in ``app/extensions/builtin/health_assistant_bridge/
    docs/api-reference.md`` in the same commit):

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

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
    "MACHINE_DETAIL_CAP",
    "RETRYABLE_VOCABULARY",
    "BridgeError",
    "ExtensionError",
    "machine_error_detail",
]

#: Cap for a typed detail that crosses an untrusted/machine boundary.
#: Mirrors the charter's 500-char detail bound but is tighter because the
#: inbound webhook/API surfaces are machine-to-machine (audit 2026-10-18 F22).
MACHINE_DETAIL_CAP = 300

#: Stable fallback detail for an untyped exception. Raw exception text never
#: crosses the boundary — the full traceback goes to the server log instead.
_INTERNAL_MACHINE_DETAIL = "Internal processing error."


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
            # 7E wire truth: the serialized detail is the bounded object;
            # the in-process `.detail` stays the capped diagnostic string.
            "detail": {"message": self.detail[:400]},
            "retryable": self.retryable,
        }
        if self.retry_after_ms is not None:
            payload["retryAfterMs"] = self.retry_after_ms
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
        super().__init__(message[:MACHINE_DETAIL_CAP])
        self.status_code = status_code
        self.code = code


def machine_error_detail(exc: BaseException, *, default_code: str = "io") -> dict[str, str]:
    """Typed ``{code, detail}`` for an exception crossing a machine surface.

    Audit 2026-10-18 F22: inbound paths (webhook, two-way API proxy, bridge
    ``/sync``) used to persist/echo ``str(exc)`` — uncapped, and sometimes
    containing internal paths, upstream URLs or provider text. Only the
    charter-typed families carry a safe detail; any other exception becomes
    the stable ``io`` class with a generic detail. The real exception is
    logged by the caller, never returned.

    Callers render the dict as their surface's wire shape (HTTP ``detail``
    or the bridge's ``error`` field) and may add the code prefix to a sync
    log row.
    """
    if isinstance(exc, ExtensionError):
        return {"code": exc.code, "detail": exc.detail[:MACHINE_DETAIL_CAP]}
    if isinstance(exc, BridgeError):
        return {"code": exc.code, "detail": str(exc)[:MACHINE_DETAIL_CAP]}
    return {"code": default_code, "detail": _INTERNAL_MACHINE_DETAIL}

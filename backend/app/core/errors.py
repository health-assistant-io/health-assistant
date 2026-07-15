"""Domain exception hierarchy (audit C1).

Service-layer code raises these instead of ``HTTPException`` so it stays free
of HTTP/FastAPI coupling and is reusable by Celery tasks, importers, and the
FHIR facade. ``app.main`` registers a single handler for :class:`DomainError`
that maps each subclass to its HTTP status with a **safe, client-facing**
``detail`` — never internal/PII/stack information (that is the global 500
handler's job, gated on ``DEBUG``).

HTTP mapping:

  NotFoundError        -> 404
  AuthorizationError   -> 403
  ValidationError      -> 400
  ConflictError        -> 409
  ConcurrencyError     -> 409  (stale-version / optimistic-lock conflict)
  DomainError (base)   -> 500  (fallback; subclasses should override)

Raise the most specific subclass; the ``detail`` is shown verbatim to the
client, so keep it user-facing (e.g. ``"Patient not found"``) and never embed
``str(exc)`` of an underlying DB/driver error.
"""
from __future__ import annotations

from typing import Optional


class DomainError(Exception):
    """Base for all service-layer domain exceptions."""

    status_code: int = 500

    def __init__(self, detail: str = "", *, status_code: Optional[int] = None):
        super().__init__(detail)
        self.detail = detail or self.__class__.__name__
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(DomainError):
    status_code = 404


class AuthorizationError(DomainError):
    status_code = 403


class ValidationError(DomainError):
    status_code = 400


class ConflictError(DomainError):
    status_code = 409


class ConcurrencyError(ConflictError):
    """Optimistic-concurrency / stale-version conflict (HTTP 409)."""

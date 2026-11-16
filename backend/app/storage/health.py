"""Cached, degrade-open storage health for the health endpoint.

The boot probe refuses a bad configuration. This module answers the runtime
question a container healthcheck asks: is the configured store still
reachable? The probe is cached so a 30-second compose healthcheck never
hammers the provider, and it is **bounded**: the provider call runs on a
short-lived daemon thread and a snapshot waits at most
:data:`PROBE_TIMEOUT_SECONDS`, so a slow SDK timeout cannot hold the request
or the probe lock open longer than the container healthcheck allows.

A transient provider failure (including a probe timeout) **degrades open**:
the endpoint stays 200 and reports ``storage.status: degraded``. It becomes
a hard failure (503) only when object storage is required (a cloud profile or
``OBJECT_STORAGE_REQUIRED=true``) or when the failure has been sustained
longer than :data:`SUSTAINED_FAILURE_SECONDS`.

Only the contract error taxonomy (``StorageError`` descendants) and
``OSError`` are health outcomes; an unexpected exception is re-raised, never
silently reported as a provider outage.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.storage.object_storage import (
    ObjectStore,
    StorageConfig,
    StorageConfigError,
    StorageError,
)

PROBE_TTL_SECONDS = 30.0
SUSTAINED_FAILURE_SECONDS = 300.0
PROBE_TIMEOUT_SECONDS = 5.0

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StorageHealth:
    backend: str
    status: str
    required: bool
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == STATUS_UNAVAILABLE

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "backend": self.backend,
            "status": self.status,
            "required": self.required,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        return body


def _run_health(store: ObjectStore, outcome: dict[str, BaseException]) -> None:
    """Run ``store.health()`` on the probe thread and hand failures back."""
    try:
        store.health()
    except BaseException as exc:  # transported to snapshot(); unexpected ones re-raised
        outcome["error"] = exc


class StorageHealthProbe:
    """Probe ``store.health()`` at most once per TTL and classify the result."""

    def __init__(
        self,
        config: StorageConfig,
        store: ObjectStore,
        *,
        ttl_seconds: float = PROBE_TTL_SECONDS,
        sustained_seconds: float = SUSTAINED_FAILURE_SECONDS,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._store = store
        self._ttl_seconds = float(ttl_seconds)
        self._sustained_seconds = float(sustained_seconds)
        self._probe_timeout_seconds = max(0.0, float(probe_timeout_seconds))
        self._clock = clock
        self._condition = threading.Condition(threading.Lock())
        self._cached: StorageHealth | None = None
        self._checked_at = 0.0
        self._checked_at_wall: datetime | None = None
        self._failing_since: float | None = None
        self._probing = False
        self._probe_started_at = 0.0

    @property
    def config(self) -> StorageConfig:
        return self._config

    @property
    def checked_at(self) -> datetime | None:
        """Wall-clock instant of the cached snapshot (operator `checked_at`).

        ``_clock`` is monotonic for TTL math; the status surface needs a
        separate UTC instant. A cached result keeps it until the TTL expires
        or :meth:`invalidate` forces a fresh probe.
        """
        with self._condition:
            return self._checked_at_wall

    def invalidate(self) -> None:
        with self._condition:
            self._cached = None
            self._checked_at = 0.0
            self._checked_at_wall = None

    def snapshot(self) -> StorageHealth:
        with self._condition:
            now = self._clock()
            cached = self._fresh(now)
            if cached is not None:
                return cached
            if self._probing:
                remaining = self._probe_timeout_seconds - (now - self._probe_started_at)
                if remaining > 0:
                    self._condition.wait(remaining)
                cached = self._fresh(self._clock())
                if cached is not None:
                    return cached
                timed_out = self._clock()
                return self._store_result(
                    timed_out,
                    self._failure(
                        timed_out,
                        TimeoutError("storage health probe is still running"),
                        fatal=False,
                    ),
                )
            self._probing = True
            self._probe_started_at = now

        outcome: dict[str, BaseException] = {}
        worker = threading.Thread(
            target=_run_health,
            args=(self._store, outcome),
            name="storage-health-probe",
            daemon=True,
        )
        worker.start()
        worker.join(self._probe_timeout_seconds)

        with self._condition:
            try:
                if worker.is_alive():
                    result = self._failure(
                        now,
                        TimeoutError(
                            f"storage health probe exceeded {self._probe_timeout_seconds:g}s"
                        ),
                        fatal=False,
                    )
                else:
                    error = outcome.get("error")
                    if error is None:
                        self._failing_since = None
                        result = StorageHealth(
                            backend=self._config.backend,
                            status=STATUS_OK,
                            required=self._config.required,
                        )
                    elif isinstance(error, (StorageError, OSError)):
                        result = self._failure(
                            now,
                            error,
                            fatal=isinstance(error, StorageConfigError),
                        )
                    else:
                        raise error
                return self._store_result(now, result)
            finally:
                self._probing = False
                self._condition.notify_all()

    def _fresh(self, now: float) -> StorageHealth | None:
        if self._cached is not None and now - self._checked_at < self._ttl_seconds:
            return self._cached
        return None

    def _store_result(self, now: float, health: StorageHealth) -> StorageHealth:
        self._cached = health
        self._checked_at = now
        self._checked_at_wall = datetime.now(UTC)
        return health

    def _failure(self, now: float, exc: BaseException, *, fatal: bool) -> StorageHealth:
        if self._failing_since is None:
            self._failing_since = now
        sustained = (now - self._failing_since) >= self._sustained_seconds
        failed = fatal or self._config.required or sustained
        return StorageHealth(
            backend=self._config.backend,
            status=STATUS_UNAVAILABLE if failed else STATUS_DEGRADED,
            required=self._config.required,
            detail=type(exc).__name__,
        )

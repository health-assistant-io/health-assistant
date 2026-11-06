"""conformance — the family connector authoring kit (ADR-0040 clause 10 / D11).

Generalizes career's shipped ``ConnectorContractTests`` to the charter
envelope and is itself vendored byte-identical from
``the family conformance contract`` (ADR-0031 sync-and-gate; never edited
in-repo). Hosts re-export this module to connector authors as THE
conformance suite; the politeness/h hostile runtime wall is separate —
hosts run ``polite_http.fixtures.json`` through the vendored runtime
(data-once fixtures, per-host executor).

Structurally typed: a conforming connector exposes ``key``,
``config_model()`` returning a validating model,
``fetch(config, state, *, transport, secrets=None)`` returning a result
with ``items`` / ``next_state`` / ``partial_errors`` (typed charter
errors: every entry carries ``code``/``detail``/``retryable``), and
``capabilities`` with ``supports_incremental``. Subclass, set
``connector``/``config``/``body`` and run with pytest:

    class TestMyConnector(ConnectorConformanceTests):
        connector = MyConnector()
        config = {"url": "https://example.com/feed"}
        body = FIXTURE_PAYLOAD

    Contract changelog (provenance only — ADR-0031 byte-identity stays the
    enforcement; CONTRACT_VERSION carries no scheme or resolution): v1 —
    the floors got teeth: statelessness asserts fetch never mutates
    config/state, the conditional-GET probe fails when the spy transport
    is never called, a 304 must yield a success-shaped result (the
    treat-"no-data"-as-error bug class), and validate_config must reject
    ``invalid_config`` (default: the empty config).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

__all__ = ["CONTRACT_VERSION", "ConnectorConformanceTests", "FakeTransportResponse"]

CONTRACT_VERSION = "1"


@dataclass
class FakeTransportResponse:
    """Transport result shape for offline contract runs — hosts may
    substitute their own response object (duck-typed attributes)."""

    status: int
    body: str = ""
    etag: str | None = None
    last_modified: str | None = None
    url: str = ""
    truncated: bool = False
    headers: dict[str, str] = field(default_factory=dict)


HttpTransport = Callable[[str, dict[str, str]], Awaitable[Any]]


def _typed_errors(result: Any) -> list[Any]:
    return list(getattr(result, "partial_errors", []) or [])


class ConnectorConformanceTests:
    """The connector contract suite every connector must pass (offline).

    Verifies the promises the downstream stack depends on: config
    round-trip, stateless idempotence, external_id stability, typed
    (never string) partial errors, capability honesty, and conditional
    GET validator passthrough.
    """

    connector: ClassVar[Any]
    config: ClassVar[dict[str, Any]]
    body: ClassVar[str]
    etag: ClassVar[str | None] = '"test-etag"'
    initial_state: ClassVar[dict[str, Any]] = {}
    # A config the connector's config model must REJECT (None → the empty
    # config: a validating model never accepts ``{}``). Set a targeted
    # invalid config only when the model legitimately defaults every field.
    invalid_config: ClassVar[dict[str, Any] | None] = None

    def transport_200(self) -> HttpTransport:
        async def _transport(url: str, headers: dict[str, str]) -> Any:
            return FakeTransportResponse(200, self.body, self.etag)

        return _transport

    def transport_304(self) -> HttpTransport:
        async def _transport(url: str, headers: dict[str, str]) -> Any:
            return FakeTransportResponse(304, "")

        return _transport

    @pytest.fixture
    def first_result(self) -> Any:
        import asyncio

        return asyncio.run(
            self.connector.fetch(
                dict(self.config),
                dict(self.initial_state),
                transport=self.transport_200(),
                secrets=dict(getattr(self, "secrets", None) or {}),
            )
        )

    def test_config_round_trip(self) -> None:
        validated = self.connector.validate_config(self.config)
        assert validated == self.connector.validate_config(validated)

    def test_invalid_config_rejected(self) -> None:
        candidate = self.invalid_config if self.invalid_config is not None else {}
        with pytest.raises(Exception):
            self.connector.validate_config(candidate)

    def test_items_present_and_valid(self, first_result: Any) -> None:
        assert not _typed_errors(first_result)
        items = list(getattr(first_result, "items", []))
        assert items, "fixture must produce at least one item"
        for item in items:
            external_id = getattr(item, "external_id", None)
            assert external_id

    def test_partial_errors_are_typed(self, first_result: Any) -> None:
        for error in _typed_errors(first_result):
            assert not isinstance(error, str), "partial_errors must be typed charter errors"
            code = getattr(error, "code", None)
            detail = getattr(error, "detail", None)
            retryable = getattr(error, "retryable", None)
            assert isinstance(code, str) and code
            assert isinstance(detail, str)
            assert retryable in ("yes", "no", "after-delay", "after-config-change")

    def test_external_ids_stable_across_runs(self, first_result: Any) -> None:
        import asyncio

        again = asyncio.run(
            self.connector.fetch(
                dict(self.config), {}, transport=self.transport_200(), secrets={}
            )
        )
        assert [getattr(p, "external_id", None) for p in getattr(again, "items", [])] == [
            getattr(p, "external_id", None) for p in getattr(first_result, "items", [])
        ]

    def test_fetch_is_stateless_and_idempotent(self, first_result: Any) -> None:
        import asyncio
        import copy

        config_arg = copy.deepcopy(self.config)
        state_arg = copy.deepcopy(dict(self.initial_state))
        config_ref = copy.deepcopy(config_arg)
        state_ref = copy.deepcopy(state_arg)
        asyncio.run(
            self.connector.fetch(config_arg, state_arg, transport=self.transport_200(), secrets={})
        )
        assert config_arg == config_ref, "fetch must not mutate its config"
        assert state_arg == state_ref, "fetch must not mutate its state"
        assert not getattr(self.connector, "key", "") == ""

    def test_capability_honesty_incremental(self) -> None:
        import asyncio

        if not self.connector.capabilities.supports_incremental:
            return
        first = asyncio.run(
            self.connector.fetch(
                dict(self.config),
                dict(self.initial_state),
                transport=self.transport_200(),
                secrets={},
            )
        )
        next_state = getattr(first, "next_state", {}) or {}
        second = asyncio.run(
            self.connector.fetch(
                dict(self.config), dict(next_state), transport=self.transport_200(), secrets={}
            )
        )
        assert getattr(second, "items", []) is not None

    def test_not_modified_yields_success_result(self) -> None:
        import asyncio

        result = asyncio.run(
            self.connector.fetch(
                dict(self.config),
                dict(self.initial_state),
                transport=self.transport_304(),
                secrets={},
            )
        )
        assert not _typed_errors(result), "304 must not surface as typed partial errors"
        assert getattr(result, "items", None) is not None, "304 must yield a success shape"

    def test_conditional_get_validators_forwarded(self) -> None:
        import asyncio

        seen: dict[str, str] = {}

        async def _spy(url: str, headers: dict[str, str]) -> Any:
            seen.update(headers)
            return FakeTransportResponse(200, self.body, self.etag)

        asyncio.run(self.connector.fetch(dict(self.config), {"etag": '"abc"'}, transport=_spy, secrets={}))
        assert seen, "connector made no transport call — conditional-GET passthrough unverified"
        assert seen.get("If-None-Match") == '"abc"'

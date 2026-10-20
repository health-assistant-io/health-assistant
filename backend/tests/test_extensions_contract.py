"""The extension-contract fixture wall (ADR-0040 clause 22 / plan-32 D5+D23).

Every host re-runs the SAME data-once fixture suites from its vendored
artifacts (implementations may differ, outputs may not):

* ``contracts/extension-contract.fixtures.json`` — structural: valid
  manifests/sources accepted, invalid ones rejected, via the vendored
  validator (``app.extensions.contract``) against the vendored schemas.
* ``contracts/polite_http.fixtures.json`` — behavioral: the D23 hostile
  list (per-hop redirect revalidation, DNS-rebind pinning, IP-literal
  obfuscations, Retry-After both forms, robots 5xx ⇒ deny, declared-domain
  scope vs CDN hops) plus the politeness happy paths, executed against the
  vendored runtime (``app.extensions.polite_http``) over an
  ``httpx.MockTransport`` — no network, deterministic clock/jitter per the
  fixtures' executor contract.
"""

from __future__ import annotations

import json
from email.utils import formatdate
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.extensions import contract, polite_http

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
MANIFEST_FIXTURES = json.loads(
    (CONTRACTS_DIR / "extension-contract.fixtures.json").read_text(encoding="utf-8")
)
POLITE_FIXTURES = json.loads(
    (CONTRACTS_DIR / "polite_http.fixtures.json").read_text(encoding="utf-8")
)
MANIFEST_SCHEMA = json.loads(
    (CONTRACTS_DIR / "extension-manifest.schema.json").read_text(encoding="utf-8")
)
SOURCES_SCHEMA = json.loads(
    (CONTRACTS_DIR / "extension-sources.schema.json").read_text(encoding="utf-8")
)


# ------------------------------------------------ structural: the Phase 0 wall


def _schema_for(group: str, case: dict[str, Any]) -> Any:
    if group == "sources" and case.get("against") == "desiredState":
        return SOURCES_SCHEMA["$defs"]["desiredStateDocument"], SOURCES_SCHEMA
    if group == "sources" and case.get("against") == "localSource":
        return SOURCES_SCHEMA["$defs"]["localSourceDescriptor"], SOURCES_SCHEMA
    if group == "bundled":
        return MANIFEST_SCHEMA["$defs"]["bundledManifest"], MANIFEST_SCHEMA
    if group == "sources":
        return SOURCES_SCHEMA, SOURCES_SCHEMA
    return MANIFEST_SCHEMA, MANIFEST_SCHEMA


def _is_manifest_group(group: str) -> bool:
    return group in ("manifest", "bundled")


def _semantics(case: dict[str, Any]) -> list[str]:
    """The canonical cross-key rules the schema cannot express (plan 35
    wave B). A fixture carrying ``reject: "semantics"`` is structurally
    valid by definition; a fixture with no channel is structural."""
    if case.get("reject") == "schema":
        return []
    return contract.manifest_i18n_semantics(case["doc"])


@pytest.mark.parametrize(
    "group,case",
    [
        pytest.param(group, case, id=f"{group}:{case['name']}")
        for group in ("manifest", "bundled", "sources")
        for case in MANIFEST_FIXTURES.get(group, {}).get("valid", [])
    ],
)
def test_structural_fixtures_accept(group: str, case: dict[str, Any]) -> None:
    target, root = _schema_for(group, case)
    assert contract.validate(target, case["doc"], root) is None, case["name"]
    if _is_manifest_group(group):
        assert _semantics(case) == [], case["name"]


@pytest.mark.parametrize(
    "group,case",
    [
        pytest.param(group, case, id=f"{group}:{case['name']}")
        for group in ("manifest", "bundled", "sources")
        for case in MANIFEST_FIXTURES.get(group, {}).get("invalid", [])
    ],
)
def test_structural_fixtures_reject(group: str, case: dict[str, Any]) -> None:
    target, root = _schema_for(group, case)
    err = contract.validate(target, case["doc"], root)
    channel = case.get("reject", "schema")
    if channel == "semantics":
        assert _is_manifest_group(group), f"{case['name']}: semantics channel outside a manifest group"
        assert err is None, f"{case['name']}: semantics channel but schema rejected ({err})"
        assert _semantics(case), f"{case['name']}: accepted by the semantic channel"
    elif channel == "schema":
        assert err is not None, f"invalid case {case['name']} was accepted by the schema channel"
    else:
        raise AssertionError(f"{case['name']}: unknown reject channel {channel!r}")


# ------------------------------------------------ behavioral: the D23 wall


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _resolver_from(spec: dict[str, Any] | None):
    gaierror_hosts = set((spec or {}).get("__gaierror__", []))
    table = {h: addrs for h, addrs in (spec or {}).items() if h != "__gaierror__"}

    def resolver(host: str) -> list[str]:
        if host in gaierror_hosts:
            raise OSError(f"no address for {host}")
        if host not in table:
            raise OSError(f"NXDOMAIN: {host}")
        return list(table[host])

    return resolver


def _config_from(spec: dict[str, Any] | None) -> polite_http.PoliteHttpConfig:
    spec = spec or {}
    guard_spec = spec.get("guard") or {}
    return polite_http.PoliteHttpConfig(
        guard=polite_http.NetGuardConfig(
            allowed_hosts=frozenset(guard_spec.get("allowedHosts", [])),
            allow_private=bool(guard_spec.get("allowPrivate", False)),
            block_private_ranges=bool(guard_spec.get("blockPrivateRanges", True)),
        ),
        domains=frozenset(spec.get("domains", [])),
        requests_per_minute=int(spec.get("requestsPerMinute", 30)),
        max_response_bytes=int(spec.get("maxResponseBytes", 4 * 1024 * 1024)),
        max_retries=int(spec.get("maxRetries", 3)),
        max_redirects=int(spec.get("maxRedirects", 5)),
        respect_robots=bool(spec.get("respectRobots", True)),
        pin_resolved_address=bool(spec.get("pinResolvedAddress", True)),
    )


class _Transport:
    """MockTransport handler implementing the fixtures' executor contract:
    first matching route wins; 'host' matches the original name via the
    Host header (the runtime pins IP hosts in URLs); 'ifCalls' bounds a
    route to the first N matching calls; '__now_plus__' templates a
    Retry-After HTTP-date relative to now (fixtures never pin wall-clock
    dates); 'repeat' builds large bodies; 'raise' simulates a transport
    failure."""

    def __init__(self, routes: list[dict[str, Any]]) -> None:
        self.routes = routes
        self.route_calls = [0] * len(routes)
        self.seen: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        host_header = request.headers.get("host", "")
        for index, route in enumerate(self.routes):
            limit = route.get("ifCalls")
            if limit is not None and self.route_calls[index] >= int(limit):
                continue
            if route.get("host") and route["host"] not in {host_header, request.url.host}:
                continue
            if route["match"] != path:
                continue
            self.route_calls[index] += 1
            self.seen.append(
                {
                    "path": path,
                    "url_host": request.url.host,
                    "host_header": host_header,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                    "sni": request.extensions.get("sni_hostname"),
                }
            )
            if route.get("raise") == "connect_error":
                raise httpx.ConnectError("simulated connection failure", request=request)
            headers = dict(route.get("headers", {}))
            retry_after = headers.get("Retry-After")
            if isinstance(retry_after, dict) and "__now_plus__" in retry_after:
                import time as _time

                headers["Retry-After"] = formatdate(
                    _time.time() + float(retry_after["__now_plus__"]), usegmt=True
                )
            body = route.get("text", "")
            if "repeat" in route:
                body = body * int(route["repeat"])
            return httpx.Response(int(route.get("status", 200)), text=body, headers=headers)
        return httpx.Response(500, text=f"no route for {path}")

    @property
    def last(self) -> dict[str, Any]:
        assert self.seen, "no transport request was recorded"
        return self.seen[-1]


def _assert_sleep(expected: list[Any], slept: list[float], where: str) -> None:
    assert len(slept) == len(expected), f"{where}: slept {slept}, expected {expected}"
    for want, got in zip(expected, slept, strict=True):
        if isinstance(want, list):
            assert want[0] <= got <= want[1], f"{where}: sleep {got} outside [{want[0]}, {want[1]}]"
        else:
            assert got == pytest.approx(want), f"{where}: sleep {got} != {want}"


@pytest.mark.parametrize("case", POLITE_FIXTURES["cases"], ids=lambda case: case["name"])
async def test_polite_http_fixture_wall(case: dict[str, Any]) -> None:
    clock = _Clock()
    transport = _Transport(case.get("routes", []))
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    limiter = polite_http.HostRateLimiter(
        requests_per_minute=_config_from(case.get("config")).requests_per_minute, clock=clock
    )
    robots = polite_http.RobotsCache(clock=clock)
    step_slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        step_slept.append(seconds)
        clock.now += seconds

    try:
        for step in case["steps"]:
            step_slept.clear()
            request = step["request"]
            expect = step.get("expect", {})
            try:
                response = await polite_http.polite_get(
                    request["url"],
                    client=client,
                    config=_config_from(case.get("config")),
                    headers=polite_http.conditional_headers(request.get("state")),
                    resolver=_resolver_from(case.get("resolver")),
                    rate=limiter,
                    robots=robots,
                    sleep=fake_sleep,
                    clock=clock,
                    rng=lambda cap: cap / 2.0,
                )
            except polite_http.PoliteHttpError as exc:
                error = expect.get("error")
                assert error is not None, (
                    f"{case['name']}: unexpected typed error {exc.code}: {exc.detail}"
                )
                assert exc.code == error["code"], case["name"]
                if "retryable" in error:
                    assert exc.retryable == error["retryable"], case["name"]
                if "retryAfterMs" in error:
                    assert exc.retry_after_ms == error["retryAfterMs"], case["name"]
            else:
                expected_response = expect.get("response")
                assert expected_response is not None, (
                    f"{case['name']}: expected an error, got status {response.status}"
                )
                if "status" in expected_response:
                    assert response.status == expected_response["status"], case["name"]
                if "etag" in expected_response:
                    assert response.etag == expected_response["etag"], case["name"]
                if "lastModified" in expected_response:
                    assert response.last_modified == expected_response["lastModified"], case["name"]
                if "truncated" in expected_response:
                    assert response.truncated == expected_response["truncated"], case["name"]
                if "url" in expected_response:
                    assert response.url == expected_response["url"], case["name"]
                if "bodyContains" in expected_response:
                    assert expected_response["bodyContains"] in response.body, case["name"]
                if "bodyLength" in expected_response:
                    assert len(response.body) == expected_response["bodyLength"], case["name"]
            if "slept" in expect:
                _assert_sleep(expect["slept"], step_slept, case["name"])
            if "sentHeaders" in expect:
                last = transport.last
                for key, value in expect["sentHeaders"].items():
                    assert last["headers"].get(key.lower()) == value, case["name"]
            if "transportHost" in expect:
                assert transport.last["url_host"] == expect["transportHost"], case["name"]
            if "hostHeader" in expect:
                assert transport.last["host_header"] == expect["hostHeader"], case["name"]
            if "sni" in expect:
                assert transport.last["sni"] == expect["sni"], case["name"]
            if "seenPaths" in expect:
                assert [entry["path"] for entry in transport.seen] == expect["seenPaths"], case[
                    "name"
                ]
    finally:
        await client.aclose()

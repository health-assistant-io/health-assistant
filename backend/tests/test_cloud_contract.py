"""The vendored cloud-connector fixture wall (ADR-0052 / plan 52).

Runs the canonical runner against the vendored fixtures and engines. A
canonical change that is not re-synced fails here before it reaches a user.
"""

from __future__ import annotations

from tests.cloud_conformance import run


def test_cloud_connector_fixture_wall() -> None:
    assert run() == 0

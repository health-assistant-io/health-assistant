"""Host-enforced extension capability grants (ADR 0030/0040/0054 D8).

The manifest is the declaration of record. These tests pin the grant shape per
tier and the refusal paths:

- T2 code receives exactly the declared capability set (consent-intersected),
  no ambient capability and no database access;
- an undeclared capability/data class is refused even when the provider object
  implements the hook (declaration, never display);
- T3 MCP tool calls map risk classes to the principal's scopes;
- T0 is data-only; T1 bundled code still gets only what it declares;
- the escape matrix fixture is still the host's wall.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.authz.permissions import Permission
from app.extensions import capabilities, isolation
from app.extensions.errors import ExtensionError
from app.services.integration.sync_support import _declared

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


def _t2_manifest(**overrides) -> dict:
    manifest = {
        "id": "demo-t2",
        "entry": "extension.py",
        "capabilities": ["net", "state"],
        "domains": ["https://api.example.com"],
        "itemSchema": {"type": "object", "properties": {"observation": {"type": "object"}}},
    }
    manifest.update(overrides)
    return manifest


def _t3_manifest() -> dict:
    return {
        "id": "demo-t3",
        "entry": "server.py",
        "mcp": {"transport": "stdio"},
        "capabilities": ["inbound-api"],
        "itemSchema": {"type": "object", "properties": {"observation": {"type": "object"}}},
    }


def test_t2_grant_is_exactly_the_declared_set_and_has_no_database_access():
    grant = capabilities.grant_for_manifest(_t2_manifest(), bundled=False)

    assert grant.tier == "T2"
    assert grant.capabilities == frozenset({"net", "state"})
    assert grant.data_classes == frozenset({"observation"})
    assert grant.database_access is False
    assert Permission.CLINICAL_WRITE in grant.permissions
    assert Permission.TENANT_MANAGE not in grant.permissions
    grant.require("state")
    with pytest.raises(ExtensionError) as exc:
        grant.require("chat-tools")
    assert exc.value.code == "unsupported"


def test_consent_intersection_narrows_an_imported_grant():
    grant = capabilities.grant_for_manifest(
        _t2_manifest(), bundled=False, granted_capabilities={"net"}
    )
    assert grant.capabilities == frozenset({"net"})
    with pytest.raises(ExtensionError):
        grant.require("state")


def test_t0_is_data_only():
    grant = capabilities.grant_for_manifest({"id": "demo-t0"}, bundled=False)
    assert grant.tier == "T0"
    assert grant.capabilities == frozenset()
    assert grant.permissions == frozenset()
    assert grant.database_access is False
    with pytest.raises(ExtensionError):
        grant.require("state")


def test_bundled_t1_grant_still_follows_the_declaration():
    manifest = capabilities.bundled_manifest("mcp_client")
    assert manifest is not None
    grant = capabilities.grant_for_manifest(manifest, bundled=True)
    assert grant.tier == "T1"
    assert grant.capabilities == frozenset({"chat-tools", "secrets"})
    assert grant.data_classes == frozenset()
    assert grant.database_access is False
    with pytest.raises(ExtensionError):
        grant.require("state")


def test_undeclared_data_class_is_refused_even_when_the_provider_implements_it():
    provider = SimpleNamespace(domain="mcp_client")
    provider.pull_documents = lambda *args, **kwargs: []

    from app.extensions.registry import integration_registry

    assert _declared(provider, "document") is False
    assert _declared(provider, "push") is False
    assert integration_registry.has_capability("mcp-client", "chat-tools") is True
    assert integration_registry.has_capability("mcp-client", "sync") is False


def test_t3_tool_risk_classes_map_to_scopes():
    grant = capabilities.grant_for_manifest(_t3_manifest(), bundled=False)
    assert grant.tier == "T3"
    assert grant.database_access is False

    read_scopes = grant.authorize_tool("read-only")
    assert Permission.PATIENT_READ in read_scopes
    write_scopes = grant.authorize_tool("state-changing")
    assert Permission.CLINICAL_WRITE in write_scopes
    with pytest.raises(ExtensionError):
        grant.authorize_tool("destructive")


def test_t3_unknown_risk_class_is_a_typed_invalid():
    grant = capabilities.grant_for_manifest(_t3_manifest(), bundled=False)
    with pytest.raises(ExtensionError) as exc:
        grant.authorize_tool("root")
    assert exc.value.code == "invalid"


def test_escape_matrix_is_still_the_host_wall():
    cases = isolation.escape_matrix(CONTRACTS_DIR)
    assert {case["group"] for case in cases} >= set(
        {
            "fs-read-outside",
            "fs-write-outside",
            "net-connect",
            "net-dns",
            "proc-spawn",
            "env-read",
            "watchdog-kill",
            "orphan-after-parent-death",
            "memory-bomb",
            "cpu-loop",
        }
    )
    by_name = {case["name"]: case for case in cases}
    assert by_name["fs-read-etc-passwd"]["expect"]["kernel"] == "denied"
    assert by_name["net-connect-arbitrary"]["expect"]["process"] == "disclosed"
    assert by_name["fs-read-etc-passwd"]["expect"]["none"] == "self-disabled"


def test_t2_grant_cannot_claim_the_system_actor():
    grant = capabilities.grant_for_manifest(
        _t2_manifest(capabilities=["secrets"], itemSchema={"type": "object", "properties": {}}),
        bundled=False,
    )
    assert grant.permissions == frozenset()
    with pytest.raises(ExtensionError):
        grant.require_permission(Permission.TENANT_MANAGE)

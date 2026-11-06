"""The version-bound consent model (plan 7 W3).

Pins the 8C consent-delta semantics against real rows:

* a version bump with a NEW capability forces the re-prompt
  (``requiresReConsent``) and the added capability has no grant;
* unchanged capabilities KEEP their grant — carried forward as new rows
  bound to the incoming digest;
* a forged consent echo is refused typed; deciding an undeclared
  capability is refused typed;
* x-secret slots seal per version: a payload stamped for one digest reads
  ``stale`` against the next one (never silently reused).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.extensions import lifecycle
from app.extensions.errors import ExtensionError
from app.models.enums import ExtensionVersionStatus
from app.models.extension_source_model import ExtensionSource, ExtensionSourceVersion
from app.services.integration import extension_consent_service as consents


def _manifest(extension_id: str, version: str, capabilities: list[str]) -> dict:
    return {
        "apiVersion": "extension/v1",
        "kindVersion": 1,
        "entry": "extension.py",
        "mcp": {"transport": "stdio"},
        "kind": "connector",
        "supportsIncremental": True,
        "id": extension_id,
        "version": version,
        "author": "Example Corp",
        "license": "MIT",
        "repository": f"https://git.example.com/ext/{extension_id}",
        "capabilities": capabilities,
        "secrets": [
            {"id": "api-token", "purpose": "Upstream token", "domains": ["api.example.com"]}
        ],
        "configSchema": {
            "type": "object",
            "properties": {
                "board-token": {"type": "string", "title": "Board token", "x-secret": True}
            },
        },
        "itemSchema": {"type": "object", "properties": {"external-id": {"type": "string"}}},
        "i18n": {
            "defaultLocale": "en",
            "locales": {"en": {"title": "Demo MCP", "summary": "A demo external MCP connector"}},
        },
    }


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def tenant(db_session):
    from tests._auth_helpers import create_user

    user = await create_user(full_name="Consent User")
    return user.tenant_id


@pytest_asyncio.fixture
async def user_id(tenant):
    from tests._auth_helpers import create_user

    user = await create_user(full_name="Consent User 2", tenant_id=tenant)
    return user.id


async def _source(db) -> ExtensionSource:
    source = ExtensionSource(
        tenant_id=None,
        kind="git",
        uri=f"https://git.example.com/ext/{uuid.uuid4().hex[:8]}.git",
        ref_spec='{"strategy": "branch", "value": "main"}',
        trust_tier="T3",
        enabled=True,
    )
    db.add(source)
    await db.flush()
    return source


async def _version(
    db, digest_seed: str, manifest: dict, *, approved: bool
) -> ExtensionSourceVersion:
    import hashlib
    import json

    digest = hashlib.sha256(digest_seed.encode()).hexdigest()
    version = ExtensionSourceVersion(
        source_id=(await _source(db)).id,
        digest=digest,
        manifest_digest=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        envelope=manifest,
        status=ExtensionVersionStatus.APPROVED if approved else ExtensionVersionStatus.PENDING,
        contract_version="extension/v1",
    )
    db.add(version)
    await db.flush()
    return version


class TestConsentDelta:
    async def test_new_capability_forces_reprompt_unchanged_keeps_grant(
        self, db_session, tenant, user_id
    ):
        v1 = await _version(
            db_session, "v1", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        v2 = await _version(
            db_session, "v2", _manifest("demo-mcp", "1.1.0", ["sync", "push"]), approved=True
        )
        await consents.record_decisions(
            db_session,
            user_id=user_id,
            tenant_id=tenant,
            version=v1,
            decisions={"sync": "granted"},
            decided_by=None,
        )

        delta = await consents.consent_delta(
            db_session, user_id=user_id, tenant_id=tenant, incoming=v2, running=v1
        )
        assert delta["capabilitiesAdded"] == ["push"], "the new capability re-prompts"
        assert delta["requiresReConsent"] is True
        assert delta["capabilitiesUnchanged"] == ["sync"]
        assert delta["keptGrants"] == ["sync"], "unchanged capability keeps its grant"

        carried = await consents.carry_over_grants(
            db_session, user_id=user_id, tenant_id=tenant, running=v1, incoming=v2
        )
        assert [row.capability for row in carried] == ["sync"]
        assert carried[0].version_id == v2.id, "the grant re-binds to the NEW digest"
        grants = await consents.granted_capabilities(db_session, user_id=user_id, version=v2)
        assert grants == {"sync"}
        assert "push" not in grants, "the added capability stays ungranted until decided"

    async def test_no_running_version_prompts_for_everything(self, db_session, tenant, user_id):
        v1 = await _version(
            db_session, "fresh", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        delta = await consents.consent_delta(
            db_session, user_id=user_id, tenant_id=tenant, incoming=v1, running=None
        )
        assert delta["capabilitiesAdded"] == ["sync"]
        assert delta["requiresReConsent"] is True
        assert delta["keptGrants"] == []

    async def test_capability_removal_reports_without_reprompt(self, db_session, tenant, user_id):
        v1 = await _version(
            db_session, "v1", _manifest("demo-mcp", "1.0.0", ["sync", "push"]), approved=True
        )
        v2 = await _version(
            db_session, "v2", _manifest("demo-mcp", "1.1.0", ["sync"]), approved=True
        )
        delta = await consents.consent_delta(
            db_session, user_id=user_id, tenant_id=tenant, incoming=v2, running=v1
        )
        assert delta["capabilitiesRemoved"] == ["push"]
        assert delta["requiresReConsent"] is False, "removals never widen permissions"

    async def test_disclosure_is_verbatim_charter_text(self, db_session, tenant, user_id):
        version = await _version(
            db_session, "d", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        delta = await consents.consent_delta(
            db_session, user_id=user_id, tenant_id=tenant, incoming=version, running=None
        )
        assert delta["disclosureKey"] == "t3PerimeterDisclosure"
        charter_text = lifecycle.disclosures()["t3PerimeterDisclosure"]
        assert delta["disclosure"] == charter_text


class TestRecordDecisions:
    async def test_forged_echo_refused_typed(self, db_session, tenant, user_id):
        version = await _version(
            db_session, "d", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        forged = {"digest": "0" * 64, "capabilities": ["sync"], "version": "1.0.0"}
        with pytest.raises(ExtensionError) as excinfo:
            await consents.record_decisions(
                db_session,
                user_id=user_id,
                tenant_id=tenant,
                version=version,
                decisions={"sync": "granted"},
                decided_by=None,
                echo=forged,
            )
        assert excinfo.value.code == "invalid"
        assert excinfo.value.detail.startswith("the consent echo does not match")

    async def test_undeclared_capability_refused_typed(self, db_session, tenant, user_id):
        version = await _version(
            db_session, "d", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        with pytest.raises(ExtensionError) as excinfo:
            await consents.record_decisions(
                db_session,
                user_id=user_id,
                tenant_id=tenant,
                version=version,
                decisions={"net": "granted"},
                decided_by=None,
            )
        assert excinfo.value.code == "invalid"
        assert "'net'" in excinfo.value.detail

    async def test_denied_decision_persists(self, db_session, tenant, user_id):
        version = await _version(
            db_session, "d", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        rows = await consents.record_decisions(
            db_session,
            user_id=user_id,
            tenant_id=tenant,
            version=version,
            decisions={"sync": "denied"},
            decided_by=None,
        )
        assert rows[0].decision.value == "denied"
        assert (
            await consents.granted_capabilities(db_session, user_id=user_id, version=version)
            == set()
        )

    async def test_pending_inbox_lists_undecided_approved_versions(
        self, db_session, tenant, user_id
    ):
        undecided = await _version(
            db_session, "a", _manifest("demo-mcp", "1.0.0", ["sync"]), approved=True
        )
        await _version(db_session, "b", _manifest("other-mcp", "2.0.0", ["sync"]), approved=False)
        decided = await _version(
            db_session, "c", _manifest("third-mcp", "3.0.0", ["sync"]), approved=True
        )
        await consents.record_decisions(
            db_session,
            user_id=user_id,
            tenant_id=tenant,
            version=decided,
            decisions={"sync": "granted"},
            decided_by=None,
        )
        pending = await consents.pending_consents(db_session, user_id=user_id, tenant_id=tenant)
        ids = {row["versionId"] for row in pending}
        assert str(undecided.id) in ids, "the undecided approved version is pending"
        assert str(decided.id) not in ids, "a decided version leaves the inbox"
        assert all("disclosure" in row for row in pending)


class TestPerVersionSealing:
    def test_declared_slots_from_envelope(self, db_session):
        manifest = _manifest("demo-mcp", "1.0.0", ["sync"])
        row = ExtensionSourceVersion(
            source_id=uuid.uuid4(),
            digest="a" * 64,
            manifest_digest="b" * 64,
            envelope=manifest,
            status=ExtensionVersionStatus.PENDING,
            contract_version="extension/v1",
        )
        slots = consents.declared_secret_slots(row)
        names = {slot["slot"] for slot in slots}
        assert names == {"api-token", "board-token"}, "manifest slots + x-secret fields"
        assert slots[0]["domains"] == ["api.example.com"], "destinations verbatim"

    def test_state_is_digest_bound(self, db_session):
        v1 = ExtensionSourceVersion(
            source_id=uuid.uuid4(),
            digest="a" * 64,
            manifest_digest="b" * 64,
            envelope={},
            status=ExtensionVersionStatus.APPROVED,
            contract_version="extension/v1",
        )
        v2 = ExtensionSourceVersion(
            source_id=uuid.uuid4(),
            digest="c" * 64,
            manifest_digest="d" * 64,
            envelope={},
            status=ExtensionVersionStatus.APPROVED,
            contract_version="extension/v1",
        )
        assert consents.version_secret_state(None, v1) == "missing"
        stamped = consents.stamp_version_secrets({"api-token": "hunter2"}, v1)
        assert consents.version_secret_state(stamped, v1) == "sealed"
        assert consents.version_secret_state(stamped, v2) == "stale", "an upgrade re-binds"
        assert "hunter2" not in str(consents.declared_secret_slots(v1))

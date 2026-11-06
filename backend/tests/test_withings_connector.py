"""The Withings OAuth2 wearable connector (plan 8 W4 item 3).

Pins the ``withings`` bundled T1 extension end to end on a fixture
Withings Health Solutions API (``httpx.MockTransport``):

* the manifest registers and declares exactly the OAuth pull profile
  (sync + secrets; observation; ``is_oauth`` config flow);
* the OAuth2 code flow works over the guarded transport: authorize URL,
  ``requesttoken`` exchange (code AND refresh), the Withings envelope
  ``{"status": <int>, "body": {...}}`` unwrapped, credential-class
  statuses typed as ``auth``;
* the token set (client secret + access/refresh pair) lives in ONE
  sealed ``oauth2`` credential slot with maintained expiry columns —
  ``user_config`` carries only the client id and the account binding;
* refresh runs BEFORE the pull when the access token is expired and
  reseals the same slot in place (rotation stamped, new pair readable);
  a failed refresh and a revoked slot both fail closed with ZERO data
  calls (engine ``credential`` class, instance ERROR);
* measure pulls land mapped types in the telemetry store with canonical
  units and a registered device, unmapped types raw with the gap
  recorded; a re-sync writes zero duplicates; the measure cursor scopes
  the next pull; hostile groups are rejected, never written;
* tenant B cannot read tenant A's pull;
* the connector adds zero migrations (no revision references a
  connector name) and the secret never persists in ``user_config``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.extensions.builtin.withings import oauth
from app.extensions.builtin.withings.provider import WithingsProvider
from app.extensions.errors import ExtensionError
from app.models.enums import IntegrationSyncStream
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel, TelemetryDeviceModel
from app.services.integration.integration_credentials import (
    CredentialError,
    read_credential,
    revoke_credential,
    seal_credential,
)
from app.services.integration.integration_sync_state import hydrate as hydrate_cursors
from app.services.integration.integration_sync_state import stream_health

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = BACKEND_DIR / "alembic" / "versions"

CLIENT_ID = "withings-partner-client-id"
CLIENT_SECRET = "withings-partner-client-secret"


class _FakeRedis:
    """Minimal async fake of the state-store slice (see test_sdk_auth)."""

    def __init__(self):
        self._store: dict[str, tuple[str, float | None]] = {}

    async def set(self, key, value, ex=None, nx=False):
        expiry = datetime.now(UTC).timestamp() + ex if ex is not None else None
        self._store[key] = (value, expiry)
        return True

    async def get(self, key):
        if key not in self._store:
            return None
        value, expiry = self._store[key]
        if expiry and datetime.now(UTC).timestamp() >= expiry:
            self._store.pop(key, None)
            return None
        return value

    async def execute_command(self, command, key, *args):
        if command != "GETDEL":
            raise NotImplementedError(command)
        value = await self.get(key)
        self._store.pop(key, None)
        return value


@pytest.fixture(autouse=True)
def _synthetic_dns(monkeypatch):
    """Synthetic hosts resolve to a fixed public address (the SSRF guard
    fails closed on resolution failure — same harness as fhir_client)."""
    from app.extensions import polite_http

    monkeypatch.setattr(polite_http, "_default_resolver", lambda host: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def _registry_manifests():
    """The engine's sync-capability gate reads the real singleton
    registry; make sure the bundled manifests (including withings) are
    loaded."""
    from app.extensions.registry import integration_registry

    if integration_registry.get_manifest("withings") is None:
        integration_registry._load_manifests()
    assert integration_registry.has_capability("withings", "sync")
    return integration_registry


# ---------------------------------------------------------------------------
# Fixture Withings API
# ---------------------------------------------------------------------------


def _epoch(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def _measure(value, type_: int, unit: int = 0) -> dict:
    return {"value": value, "type": type_, "unit": unit}


def _measuregrp(grp_id: int, created: int, measures: list) -> dict:
    return {
        "grpid": grp_id,
        "attrib": 0,
        "date": created,
        "created": created,
        "category": 1,
        "deviceid": "withings-scale-1",
        "measures": measures,
    }


def _token_body(**overrides) -> dict:
    body = {
        "userid": 363,
        "access_token": "access-token-1",
        "refresh_token": "refresh-token-1",
        "scope": "user.metrics",
        "expires_in": 10800,
        "token_type": "Bearer",
    }
    body.update(overrides)
    return body


def _fixture_server(calls: list | None = None, **overrides):
    """A MockTransport Withings API: ``/v2/oauth2`` answers queued token
    responses (then a default success), ``/measure`` answers
    ``measure_body``. Every call is recorded as ``(path, form)``."""

    token_responses: list[httpx.Response] = list(overrides.pop("token_responses", []))
    measure_body = overrides.pop(
        "measure_body",
        {"status": 0, "body": {"measuregrps": [], "more": False, "offset": 0}},
    )
    assert not overrides, f"unknown fixture kwargs: {sorted(overrides)}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = parse_qs(request.content.decode("utf-8"))
        if calls is not None:
            calls.append((path, form))
        if path == "/v2/oauth2":
            if token_responses:
                return token_responses.pop(0)
            return httpx.Response(200, json={"status": 0, "body": _token_body()})
        if path == "/measure":
            return httpx.Response(200, json=measure_body)
        return httpx.Response(404, json={"status": 2555})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(client: httpx.AsyncClient) -> WithingsProvider:
    from app.extensions.sdk.auth import OAuthStateStore

    provider = WithingsProvider()
    provider._http_client = client
    provider._state_store = OAuthStateStore(redis_client=_FakeRedis())
    return provider


async def _make_identity(config: dict, *, provider: str = "withings"):
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_integration import UserIntegration
    from app.models.user_model import UserModel

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name=f"T {tenant_id.hex[:6]}",
                slug=f"with-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"with-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Withings", "given": ["Pull"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider=provider,
                instance_name=f"withings-{tenant_id.hex[:6]}",
                status="PENDING",
                user_config=config,
            )
        )
        await db.commit()
    return tenant_id, patient_id, integration_id


def _default_config() -> dict:
    return {
        "withings_client_id": CLIENT_ID,
        "initial_window_days": 90,
        "sync_interval": 60,
    }


@pytest_asyncio.fixture
async def integration():
    from app.models.user_integration import UserIntegration

    _tenant_id, _patient_id, integration_id = await _make_identity(_default_config())
    async with AsyncSessionLocal() as db:
        yield (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


async def _load_integration(integration_id) -> object:
    from app.models.user_integration import UserIntegration

    async with AsyncSessionLocal() as db:
        return (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


async def _load_integration_in(db: AsyncSession, integration_id):
    from app.models.user_integration import UserIntegration

    return (
        await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
    ).scalar_one()


async def _run_sync(integration_row, client: httpx.AsyncClient):
    from app.services import integration_sync_service as svc

    with (
        patch.object(svc, "_try_acquire_lock", AsyncMock(return_value=(True, "test-lock"))),
        patch.object(svc, "_release_lock", AsyncMock()),
        patch.object(svc, "post_sync_notifications", AsyncMock()),
    ):
        async with AsyncSessionLocal() as db:
            fresh = await _load_integration_in(db, integration_row.id)
            return await svc.run_sync(db, fresh, _provider(client), source="test")


async def _seal_oauth_slot(
    integration_row,
    *,
    access: str = "access-token-1",
    refresh: str = "refresh-token-1",
    secret: str = CLIENT_SECRET,
    access_expires_at: datetime | None = None,
    refresh_expires_at: datetime | None = None,
):
    async with AsyncSessionLocal() as db:
        await seal_credential(
            db,
            integration_row,
            "oauth2",
            {"client_secret": secret, "access_token": access, "refresh_token": refresh},
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        await db.commit()


def _live_slot() -> datetime:
    return datetime.now(UTC) + timedelta(hours=2)


async def _count(db: AsyncSession, model, tenant_id) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Manifest / registry / wiring contract
# ---------------------------------------------------------------------------


def test_manifest_registers_and_declares_the_oauth_profile(_registry_manifests):
    manifest = _registry_manifests.get_manifest("withings")
    assert manifest and manifest["id"] == "withings"
    assert manifest["kind"] == "connector"
    assert manifest["docs"] == "docs/docs-tree.json"
    assert _registry_manifests.has_capability("withings", "sync")
    assert _registry_manifests.has_capability("withings", "secrets")
    assert not _registry_manifests.has_capability("withings", "push"), (
        "the wearable connector is pull-only — no push capability"
    )
    assert _registry_manifests.declared_data_classes("withings") == frozenset({"observation"})
    from app.extensions.registry import integration_registry

    if integration_registry.get_config_flow("withings") is None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            integration_registry._load_integration("withings")
        )
    config_flow = integration_registry.get_config_flow("withings")
    assert config_flow is not None and config_flow.is_oauth, (
        "the OAuth round-trip is opt-in via the config flow"
    )


def test_no_migration_references_connector_names():
    """A connector is an extension + rows, never DDL: no alembic revision
    may reference a connector name (plan 8 §5 definition of done)."""
    hits = []
    for revision in sorted(ALEMBIC_VERSIONS.glob("*.py")):
        body = revision.read_text(encoding="utf-8").lower()
        if "withings" in body:
            hits.append(revision.name)
    assert hits == []


# ---------------------------------------------------------------------------
# OAuth2 over the guarded transport
# ---------------------------------------------------------------------------


def test_authorize_url_carries_the_code_flow_params():
    url = oauth.build_authorize_url(CLIENT_ID, "https://app.example/cb", "state-123")
    parsed = urlparse(url)
    assert parsed.scheme == "https" and parsed.hostname == "account.withings.com"
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == [CLIENT_ID]
    assert qs["redirect_uri"] == ["https://app.example/cb"]
    assert qs["state"] == ["state-123"]
    assert qs["scope"] == ["user.metrics"]


@pytest.mark.asyncio
async def test_token_exchange_posts_the_requesttoken_envelope():
    calls: list = []
    client = _fixture_server(calls)
    body = await oauth.exchange_code(
        client, "code-1", "https://app.example/cb", CLIENT_ID, CLIENT_SECRET
    )
    assert body["access_token"] == "access-token-1"
    path, form = calls[0]
    assert path == "/v2/oauth2"
    assert form["action"] == ["requesttoken"], "the Withings v2 token action"
    assert form["grant_type"] == ["authorization_code"]
    assert form["client_id"] == [CLIENT_ID]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert form["code"] == ["code-1"]
    assert form["redirect_uri"] == ["https://app.example/cb"]

    body = await oauth.refresh_tokens(client, "refresh-token-1", CLIENT_ID, CLIENT_SECRET)
    path, form = calls[1]
    assert form["action"] == ["requesttoken"]
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-token-1"]
    await client.aclose()


@pytest.mark.asyncio
async def test_credential_envelopes_and_oauth_errors_are_typed_auth():
    client = _fixture_server(
        token_responses=[
            httpx.Response(200, json={"status": 343, "body": {}}),
            httpx.Response(400, json={"error": "invalid_grant", "error_description": "spent"}),
            httpx.Response(200, json={"status": 0, "body": {}}),
            httpx.Response(200, json={"status": 601, "body": {}}),
            httpx.Response(200, json={"status": 283, "body": {}}),
        ]
    )
    with pytest.raises(ExtensionError) as exc_info:
        await oauth.refresh_tokens(client, "r", CLIENT_ID, CLIENT_SECRET)
    assert exc_info.value.code == "auth", "343 is the expired/invalid access token status"

    with pytest.raises(ExtensionError) as exc_info:
        await oauth.refresh_tokens(client, "r", CLIENT_ID, CLIENT_SECRET)
    assert exc_info.value.code == "auth", "an RFC 6749 error body is the typed auth rejection"

    with pytest.raises(ExtensionError) as exc_info:
        await oauth.refresh_tokens(client, "r", CLIENT_ID, CLIENT_SECRET)
    assert exc_info.value.code == "auth", "a token body without access_token never passes"

    with pytest.raises(ExtensionError) as exc_info:
        await oauth.refresh_tokens(client, "r", CLIENT_ID, CLIENT_SECRET)
    assert exc_info.value.code == "rate-limited", "the in-envelope rate limit is typed"

    with pytest.raises(ExtensionError) as exc_info:
        await oauth.refresh_tokens(client, "r", CLIENT_ID, CLIENT_SECRET)
    assert exc_info.value.code == "auth", "a revoked authorization is credential-class"
    await client.aclose()


@pytest.mark.asyncio
async def test_normalize_token_stamps_both_expiry_columns():
    now = datetime.now(UTC)
    payload, access_exp, refresh_exp = oauth.normalize_token(_token_body(expires_in=10800))
    assert payload["access_token"] == "access-token-1"
    assert payload["refresh_token"] == "refresh-token-1"
    assert payload["scope"] == "user.metrics"
    assert timedelta(seconds=10795) < access_exp - now < timedelta(seconds=10805)
    assert refresh_exp - now > timedelta(days=300), "the conservative refresh floor"
    assert "client_secret" not in payload, "the secret joins at reseal time, not here"


# ---------------------------------------------------------------------------
# The OAuth round-trip: seal, exchange, binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_oauth_seals_the_full_slot_and_records_the_binding(integration):
    from app.models.user_integration import IntegrationCredential

    async with AsyncSessionLocal() as db:
        await seal_credential(db, integration, "oauth2", {"client_secret": CLIENT_SECRET})
        await db.commit()

    calls: list = []
    client = _fixture_server(calls)
    provider = _provider(client)
    redirect_uri = "https://app.example/api/v1/integrations/withings/oauth/callback"
    authorize_url, state = await provider.begin_oauth(integration, redirect_uri)
    assert "response_type=code" in authorize_url

    pending = await provider._state_store.consume(state)
    assert pending and pending["redirect_uri"] == redirect_uri

    result = await provider.complete_oauth(integration, pending, "code-1")
    assert result == {"userid": "363"}
    assert [path for path, _form in calls] == ["/v2/oauth2"], "exactly the token exchange"

    # The route is the committer (the callback endpoint commits after the
    # provider mutates the row) — model that boundary here.
    async with AsyncSessionLocal() as db:
        await db.merge(integration)
        await db.commit()

    async with AsyncSessionLocal() as db:
        payload = await read_credential(db, integration, "oauth2")
        assert payload["client_secret"] == CLIENT_SECRET, "the secret carries forward"
        assert payload["access_token"] == "access-token-1"
        assert payload["refresh_token"] == "refresh-token-1"
        row = (
            (
                await db.execute(
                    select(IntegrationCredential).where(
                        IntegrationCredential.user_integration_id == integration.id,
                        IntegrationCredential.kind == "oauth2",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert row.access_expires_at is not None and row.refresh_expires_at is not None
        assert row.access_expires_at > datetime.now(UTC)

    fresh = await _load_integration(integration.id)
    assert fresh.user_config.get("withings_userid") == "363", "non-secret binding in config"
    assert fresh.user_config.get("withings_scope") == "user.metrics"
    for key in ("access_token", "refresh_token", "client_secret"):
        assert key not in fresh.user_config, f"{key} never persists in user_config"
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_oauth_without_a_sealed_secret_fails_closed(integration):
    calls: list = []
    client = _fixture_server(calls)
    provider = _provider(client)
    redirect_uri = "https://app.example/cb"
    _url, state = await provider.begin_oauth(integration, redirect_uri)
    pending = await provider._state_store.consume(state)

    with pytest.raises(CredentialError):
        await provider.complete_oauth(integration, pending, "code-1")
    assert calls == [], "no exchange is attempted without the sealed client secret"
    await client.aclose()


# ---------------------------------------------------------------------------
# Refresh before pull — rotation/reseal, fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_runs_before_the_pull_and_reseals_in_place(integration):
    from app.models.user_integration import IntegrationCredential

    expired_at = datetime.now(UTC) - timedelta(minutes=10)
    await _seal_oauth_slot(
        integration,
        access="stale-token",
        access_expires_at=expired_at,
    )
    calls: list = []
    client = _fixture_server(
        calls,
        token_responses=[
            httpx.Response(
                200,
                json={"status": 0, "body": _token_body(access_token="fresh-token")},
            )
        ],
    )

    provider = _provider(client)
    token = await provider._live_token(integration)
    assert token == "fresh-token", "the NEW access token is returned"

    assert [path for path, _form in calls] == ["/v2/oauth2"], "refresh BEFORE any pull"

    async with AsyncSessionLocal() as db:
        payload = await read_credential(db, integration, "oauth2")
        assert payload["access_token"] == "fresh-token", "the slot was resealed"
        assert payload["refresh_token"] == "refresh-token-1"
        assert payload["client_secret"] == CLIENT_SECRET, "the secret carries through rotation"
        row = (
            (
                await db.execute(
                    select(IntegrationCredential).where(
                        IntegrationCredential.user_integration_id == integration.id,
                        IntegrationCredential.kind == "oauth2",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert row.rotated_at is not None, "the rotation is stamped as data"
        assert row.access_expires_at is not None and row.access_expires_at > datetime.now(UTC)
        assert row.revoked_at is None, "reseal clears a revocation"
    await client.aclose()


@pytest.mark.asyncio
async def test_fresh_token_skips_the_refresh_call(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    calls: list = []
    client = _fixture_server(
        calls,
        measure_body={"status": 0, "body": {"measuregrps": [], "more": False}},
    )
    provider = _provider(client)
    observations = await provider.pull_data(integration)
    assert observations == []
    assert [path for path, _form in calls] == ["/measure"], (
        "a live access token means zero token-endpoint calls"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_refresh_fails_closed_before_any_data_call(integration):
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog

    expired_at = datetime.now(UTC) - timedelta(minutes=10)
    await _seal_oauth_slot(integration, access_expires_at=expired_at)
    calls: list = []
    client = _fixture_server(
        calls,
        token_responses=[
            httpx.Response(200, json={"status": 343, "body": {}}),
            httpx.Response(200, json={"status": 343, "body": {}}),
        ],
    )
    provider = _provider(client)
    with pytest.raises(CredentialError):
        await provider.pull_data(integration)
    assert [path for path, _form in calls] == ["/v2/oauth2"], (
        "the refresh failed — the measure endpoint is NEVER called"
    )

    result = await _run_sync(integration, client)
    assert result.status == "failed"
    assert result.error_type == "auth"
    async with AsyncSessionLocal() as db:
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs[-1].status == "failed"
        assert logs[-1].error_class.value == "credential", "the sync log names the class"
        row = await _load_integration_in(db, integration.id)
        assert row.status == IntegrationStatus.ERROR, "the instance flips to ERROR"
    assert "/measure" not in [path for path, _form in calls], (
        "the engine's own attempt also failed closed — still zero data calls"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_refresh_column_fails_closed_at_the_engine_gate(integration):
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog

    await _seal_oauth_slot(
        integration,
        access_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        refresh_expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    calls: list = []
    client = _fixture_server(calls)
    result = await _run_sync(integration, client)
    assert result.status == "failed" and result.error_type == "auth"
    assert calls == [], "an expired refresh token never reaches the provider"
    async with AsyncSessionLocal() as db:
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs[-1].error_class.value == "credential"
        row = await _load_integration_in(db, integration.id)
        assert row.status == IntegrationStatus.ERROR
    await client.aclose()


@pytest.mark.asyncio
async def test_revoked_slot_fails_closed_and_never_calls_http(integration):
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog

    await _seal_oauth_slot(integration)
    async with AsyncSessionLocal() as db:
        assert await revoke_credential(db, integration, "oauth2") == 1
        await db.commit()
        with pytest.raises(CredentialError):
            await read_credential(db, integration, "oauth2")

    calls: list = []
    client = _fixture_server(calls)
    result = await _run_sync(integration, client)
    assert result.status == "failed" and result.error_type == "auth"
    assert calls == [], "revocation stops data flow — zero HTTP calls"
    async with AsyncSessionLocal() as db:
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs[-1].error_class.value == "credential"
        row = await _load_integration_in(db, integration.id)
        assert row.status == IntegrationStatus.ERROR
    await client.aclose()


# ---------------------------------------------------------------------------
# The measure pull → telemetry funnel
# ---------------------------------------------------------------------------


def _happy_measure_body() -> dict:
    created_hr = _epoch(2026, 10, 20, 8, 0, 0)
    created_wt = _epoch(2026, 10, 20, 7, 30, 0)
    return {
        "status": 0,
        "body": {
            "updatetime": str(_epoch(2026, 10, 20, 9, 0, 0)),
            "measuregrps": [
                # Heart rate: 72 bpm — auto-creates a telemetry-flagged biomarker.
                _measuregrp(1, created_hr, [_measure(72, type_=11)]),
                # Weight: 7950 * 10**-2 = 79.5 kg — LOINC 29463-7, FHIR-routed.
                _measuregrp(2, created_wt, [_measure(7950, type_=1, unit=-2)]),
                # Unmapped Withings type 59 — kept raw with the gap recorded.
                _measuregrp(3, created_wt, [_measure(12, type_=59)]),
            ],
            "more": False,
            "offset": 0,
        },
    }


@pytest.mark.asyncio
async def test_happy_path_lands_telemetry_canonical_units_and_the_device(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    calls: list = []
    client = _fixture_server(calls, measure_body=_happy_measure_body())

    result = await _run_sync(integration, client)

    assert result.status == "partial", "the unmapped measure is a recorded gap, not a failure"
    assert result.telemetry_persisted == 1, "heart rate rides the telemetry stream"
    assert result.fhir_persisted == 2, "weight (mapped) + the unmapped raw row land as FHIR"

    async with AsyncSessionLocal() as db:
        # Device registration: one device row for the Withings source.
        devices = (
            (
                await db.execute(
                    select(TelemetryDeviceModel).where(
                        TelemetryDeviceModel.tenant_id == integration.tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(devices) == 1
        assert devices[0].source == "withings"
        assert devices[0].external_ref == integration.instance_name

        # Canonical telemetry row: canonical + raw pairs kept.
        telemetry = (
            (
                await db.execute(
                    select(TelemetryDataModel).where(
                        TelemetryDataModel.tenant_id == integration.tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [t.slug for t in telemetry] == ["heart-rate"]
        assert telemetry[0].value == 72.0
        assert telemetry[0].unit == "bpm", "the canonical unit is stored on the row"
        assert telemetry[0].raw_value == 72.0 and telemetry[0].raw_unit == "bpm"
        assert telemetry[0].patient_id == integration.patient_id

        observations = (
            (
                await db.execute(
                    select(Observation).where(Observation.tenant_id == integration.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        by_code = {}
        for obs in observations:
            coding = obs.code["coding"][0]
            by_code[coding["code"]] = obs
        assert set(by_code) == {"29463-7", "withings-measure-type-59"}
        weight = by_code["29463-7"]
        assert weight.biomarker_id is not None, "weight maps to the catalog"
        assert weight.value_quantity["value"] == 79.5, "value * 10**unit unscaled"
        assert weight.value_quantity["code"] == "kg"
        raw = by_code["withings-measure-type-59"]
        assert raw.biomarker_id is None, "the unmapped type stays raw"
        assert raw.value_quantity["value"] == 12.0

        log = (
            (
                await db.execute(
                    text(
                        "select error_class, error_message from integration_sync_logs "
                        "where integration_id = :i order by started_at desc limit 1"
                    ),
                    {"i": str(integration.id)},
                )
            )
            .mappings()
            .first()
        )
        assert log is not None and log["error_class"] == "mapping_gap"
        assert "withings-measure-type-59" in (log["error_message"] or ""), (
            "the gap names the unmapped measure type"
        )

        # The measure cursor advanced to the newest group creation stamp.
        fresh = await _load_integration_in(db, integration.id)
        await hydrate_cursors(db, fresh)
        health = {(row["stream"], row["cursor_key"]): row for row in await stream_health(db, fresh)}
    assert ("telemetry", "measure") in health
    assert health[("telemetry", "measure")]["cursor_value"] == str(_epoch(2026, 10, 20, 8, 0, 0))
    await client.aclose()


@pytest.mark.asyncio
async def test_resync_writes_zero_duplicates(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    client = _fixture_server(measure_body=_happy_measure_body())

    first = await _run_sync(integration, client)
    assert first.telemetry_persisted == 1 and first.fhir_persisted == 2

    # The fixture server ignores the cursor on purpose: the second sync
    # re-pulls the SAME groups and must write zero duplicates. The status
    # stays partial: the unmapped measure type is still unmapped, and the
    # gap stays VISIBLE on every sync until the catalog defines it.
    second = await _run_sync(integration, client)
    assert second.status == "partial"
    assert second.telemetry_persisted == 0, "telemetry dedups on (timestamp, device, slug)"
    assert second.fhir_persisted == 0, "observations dedup on their natural key"

    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 2
        assert await _count(db, TelemetryDataModel, integration.tenant_id) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cursor_scopes_the_next_pull_window(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    calls: list = []
    client = _fixture_server(calls, measure_body=_happy_measure_body())

    await _run_sync(integration, client)
    await _run_sync(integration, client)

    measure_calls = [form for path, form in calls if path == "/measure"]
    assert len(measure_calls) == 2
    first_form, second_form = measure_calls[0], measure_calls[1]
    assert "startdate" in first_form and "enddate" in first_form, (
        "the first pull covers the configured window"
    )
    assert "lastupdate" not in first_form
    assert second_form.get("lastupdate") == [str(_epoch(2026, 10, 20, 8, 0, 0))], (
        "the next pull asks only for groups created after the last success"
    )
    assert first_form.get("category") == ["1"], "real measurements only"
    await client.aclose()


@pytest.mark.asyncio
async def test_envelope_status_100_is_an_empty_pull_not_an_error(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    client = _fixture_server(measure_body={"status": 100, "body": {}})
    result = await _run_sync(integration, client)
    assert result.status == "success", "a quiet scale is a successful empty pull"
    assert result.telemetry_persisted == 0 and result.fhir_persisted == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_hostile_groups_are_rejected_and_never_written(integration):
    from app.models.user_integration import IntegrationDebugLog

    oversized = _measuregrp(9, _epoch(2026, 10, 20, 6, 0, 0), [])
    oversized["measures"] = [{"value": "x" * 450_000, "type": 1, "unit": 0}]
    bad_value = _measuregrp(
        10,
        _epoch(2026, 10, 20, 6, 30, 0),
        [_measure("abc", type_=1)],
    )
    no_date = {"grpid": 11, "measures": [_measure(80, type_=11)]}
    good = _measuregrp(12, _epoch(2026, 10, 20, 7, 0, 0), [_measure(75, type_=11)])

    body = {
        "status": 0,
        "body": {
            "measuregrps": [oversized, "not-even-a-dict", bad_value, no_date, good],
            "more": False,
        },
    }

    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        row.is_debug_enabled = True
        await db.commit()

    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    client = _fixture_server(measure_body=body)
    result = await _run_sync(integration, client)

    assert result.telemetry_persisted == 1, "only the well-formed group lands"
    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 0, (
            "oversized/wrong-typed/broken entries never land as rows"
        )
        telemetry = (
            (
                await db.execute(
                    select(TelemetryDataModel).where(
                        TelemetryDataModel.tenant_id == integration.tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [t.value for t in telemetry] == [75.0]
        rejections = (
            (
                await db.execute(
                    select(IntegrationDebugLog).where(
                        IntegrationDebugLog.integration_id == integration.id,
                        IntegrationDebugLog.title.contains("rejected"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rejections, "the rejections are counted, not swallowed"
        counts = rejections[0].payload["rejected"]
        assert counts.get("oversized") == 1
        assert counts.get("wrong_type") == 1
        assert counts.get("bad_measures") == 2, "the broken value AND the dateless group"
    await client.aclose()


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_b_cannot_read_tenant_as_pull(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    client = _fixture_server(measure_body=_happy_measure_body())
    await _run_sync(integration, client)

    tenant_b, _, integration_b = await _make_identity(_default_config())
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Observation).where(Observation.tenant_id == tenant_b))
        ).scalars().all() == [], "tenant B sees none of tenant A's pulled observations"
        assert await _count(db, TelemetryDataModel, tenant_b) == 0
        assert await _count(db, TelemetryDeviceModel, tenant_b) == 0, (
            "not even the device registry leaks"
        )

    row_b = await _load_integration(integration_b)
    await _seal_oauth_slot(row_b, access_expires_at=_live_slot())
    result = await _run_sync(row_b, client)
    assert result.telemetry_persisted == 1 and result.fhir_persisted == 2
    async with AsyncSessionLocal() as db:
        assert await _count(db, Observation, integration.tenant_id) == 2
        assert await _count(db, Observation, tenant_b) == 2, (
            "tenant B's own sync writes under tenant B only"
        )
    await client.aclose()


# ---------------------------------------------------------------------------
# Config flow: the secret never persists in user_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_seals_the_secret_and_keeps_user_config_clean(integration):
    from app.extensions.builtin.withings.config_flow import WithingsConfigFlow

    flow = WithingsConfigFlow()
    assert flow.get_secret_fields() == [], "no secret ever rides the user_config path"

    validated = await flow.validate_input(
        {
            "instance_name": "Bathroom scale",
            "withings_client_id": CLIENT_ID,
            "withings_client_secret": f"  {CLIENT_SECRET}  ",
            "initial_window_days": 90,
        }
    )
    stored = await flow.prepare_for_storage(validated, context=str(integration.id))
    assert "withings_client_secret" not in stored
    assert stored["_pending_credential"]["payload"] == {"client_secret": CLIENT_SECRET}

    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        await flow.store_credentials(db, row, stored)
        await db.commit()
        assert "_pending_credential" not in (row.user_config or {}), (
            "the transient slot is stripped before commit"
        )
        assert await read_credential(db, integration, "oauth2") == {"client_secret": CLIENT_SECRET}

    # Rotation: a new value reseals in place; a blank value keeps the old.
    stored2 = await flow.prepare_for_storage(
        {**validated, "withings_client_secret": "rotated-secret"},
        context=str(integration.id),
    )
    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        await flow.store_credentials(db, row, stored2)
        await db.commit()
        payload = await read_credential(db, integration, "oauth2")
        assert payload["client_secret"] == "rotated-secret"

    # An update that omits the field (the UI's blank secret) keeps the slot.
    without_secret = {k: v for k, v in validated.items() if k != "withings_client_secret"}
    stored3 = await flow.prepare_for_storage(without_secret, context=str(integration.id))
    assert "_pending_credential" not in stored3, "blank secret on update keeps the sealed slot"
    async with AsyncSessionLocal() as db:
        assert (await read_credential(db, integration, "oauth2"))[
            "client_secret"
        ] == "rotated-secret"

    # And the OAuth tokens join the SAME slot on authorization.
    async with AsyncSessionLocal() as db:
        row = await _load_integration_in(db, integration.id)
        await seal_credential(
            db,
            row,
            "oauth2",
            {"client_secret": "rotated-secret", "access_token": "a", "refresh_token": "r"},
        )
        await db.commit()
        payload = await read_credential(db, integration, "oauth2")
    assert set(payload) == {"client_secret", "access_token", "refresh_token"}, (
        "ONE oauth2 slot carries the whole credential set"
    )


@pytest.mark.asyncio
async def test_config_flow_validates_input():
    from app.extensions.builtin.withings.config_flow import WithingsConfigFlow

    flow = WithingsConfigFlow()
    with pytest.raises(ValueError, match="Client ID"):
        await flow.validate_input({"withings_client_id": "  "})
    with pytest.raises(ValueError, match="non-empty"):
        await flow.validate_input({"withings_client_id": "cid", "withings_client_secret": "   "})
    with pytest.raises(ValueError, match="positive integer"):
        await flow.validate_input({"withings_client_id": "cid", "initial_window_days": 0})
    validated = await flow.validate_input(
        {"withings_client_id": f"  {CLIENT_ID}  ", "initial_window_days": 30}
    )
    assert validated["withings_client_id"] == CLIENT_ID


# ---------------------------------------------------------------------------
# Provider surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connection_reports_each_layer(integration):
    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    calls: list = []
    client = _fixture_server(
        calls,
        measure_body={"status": 0, "body": {"measuregrps": [], "more": False}},
    )
    provider = _provider(client)
    result = await provider.execute_custom_action(integration, "check_connection")
    assert "Connection OK" in result["message"]

    with pytest.raises(NotImplementedError):
        await provider.execute_custom_action(integration, "nonexistent")

    # A broken credential layer fails the check before any HTTP call.
    async with AsyncSessionLocal() as db:
        await revoke_credential(db, integration, "oauth2")
        await db.commit()
    calls.clear()
    fresh = await _load_integration(integration.id)
    result = await provider.execute_custom_action(fresh, "check_connection")
    assert "Credential check failed" in result["message"]
    assert calls == [], "the check stops at the credential layer, before HTTP"
    await client.aclose()


@pytest.mark.asyncio
async def test_reset_cursors_action_clears_the_measure_cursor(integration):
    from app.services.integration.integration_sync_state import flush as flush_cursors

    await _seal_oauth_slot(integration, access_expires_at=_live_slot())
    client = _fixture_server(measure_body=_happy_measure_body())
    await _run_sync(integration, client)

    provider = _provider(client)
    # The action endpoint's boundary: hydrate → run → flush in one transaction.
    async with AsyncSessionLocal() as db:
        fresh = await _load_integration_in(db, integration.id)
        await hydrate_cursors(db, fresh)
        result = await provider.execute_custom_action(fresh, "reset_cursors")
        assert "Cursors reset" in result["message"]
        await flush_cursors(db, fresh)
        await db.commit()

    async with AsyncSessionLocal() as db:
        fresh = await _load_integration_in(db, integration.id)
        await hydrate_cursors(db, fresh)
        assert provider.get_cursor(fresh, IntegrationSyncStream.TELEMETRY, key="measure") in (
            None,
            "",
        ), "the next sync re-pulls the full window"
    await client.aclose()


# ---------------------------------------------------------------------------
# Mapper adapters (pure)
# ---------------------------------------------------------------------------


def test_mapper_unscales_maps_and_counts():
    from app.extensions.builtin.withings.mappers import measuregrp_to_observations

    grp = _measuregrp(
        1,
        _epoch(2026, 10, 20, 8, 0, 0),
        [
            _measure(7950, type_=1, unit=-2),
            _measure(72, type_=11),
            _measure(180, type_=4),
            _measure(12, type_=59),
            _measure("abc", type_=1),
        ],
    )
    observations, counters = measuregrp_to_observations(grp, patient_id="pt-1")
    assert len(observations) == 3
    weight = next(o for o in observations if o["code"]["coding"][0]["code"] == "29463-7")
    assert weight["valueQuantity"] == {
        "value": 79.5,
        "unit": "kg",
        "system": "http://unitsofmeasure.org",
        "code": "kg",
    }
    assert weight["code"]["text"] == "Body weight", "the display seeds catalog auto-create"
    hr = next(o for o in observations if o["code"]["coding"][0]["code"] == "8867-4")
    assert hr["valueQuantity"]["value"] == 72.0
    raw = next(
        o for o in observations if o["code"]["coding"][0]["system"].startswith("https://wbsapi")
    )
    assert raw["code"].get("text") is None, "the unmapped type carries no display to auto-create"
    assert counters == {"excluded_height": 1, "unmapped": 1, "bad_measures": 1}


def test_mapper_group_without_a_date_yields_nothing():
    from app.extensions.builtin.withings.mappers import measuregrp_to_observations

    observations, counters = measuregrp_to_observations(
        {"grpid": 1, "measures": [_measure(72, type_=11)]}, patient_id="pt-1"
    )
    assert observations == []
    assert counters["bad_measures"] == 1, "time-attribution is not fabricatable"


def test_mapper_cursor_epoch_is_typed():
    from app.extensions.builtin.withings.mappers import group_created_epoch

    assert group_created_epoch({"created": 1767000000}) == 1767000000
    assert group_created_epoch({"created": "1767000000"}) is None
    assert group_created_epoch({}) is None

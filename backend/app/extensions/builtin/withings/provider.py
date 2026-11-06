"""Withings connector — the OAuth2 wearable (credential + sync-state proof).

Connects a Withings account through the user's own Withings partner app
and pulls the ``measure`` categories (weight, body composition, blood
pressure, heart rate, SpO2, temperature) into the ingest funnel: mapped
measure types ride the telemetry stream with canonical units per
``BiomarkerDefinition`` and a registered ``telemetry_devices`` row; an
unmapped type lands as a raw observation with the mapping gap recorded —
never dropped silently. Sleep and activity endpoints are deliberately not
wired (no catalog mappers exist yet — flagged, not half-wired).

This connector is the OAuth proof of the connector framework:

- **Secrets**: the OAuth2 client secret, access token and refresh token
  live in ONE sealed ``integration_credentials`` slot (kind ``oauth2``,
  context-bound) — ``user_config`` carries only the non-secret client id
  and, after authorization, the Withings user binding. The row's
  ``access_expires_at``/``refresh_expires_at`` columns are maintained on
  every seal and reseal.
- **Refresh before pull**: when the access token is expired (or expiring
  within the leeway), the provider refreshes it FIRST and reseals the
  same slot in place (rotation stamped, revocation cleared). A failed
  refresh (``invalid_grant`` — the user revoked access server-side) and a
  missing/revoked slot both raise the typed :class:`CredentialError`
  BEFORE any data call — the sync fails closed, the instance flips to
  ERROR with a reconnect prompt.
- **Cursors**: the measure category keeps its ``lastupdate``-style cursor
  (the newest group ``created`` epoch seen) in ``integration_sync_state``
  (stream ``telemetry``, key ``measure``); the engine's ``next_retry_at``
  backoff honors the provider ``Retry-After`` on 429 (the guarded runtime
  surfaces it; the engine writes it as data).
- **Transport**: every call rides the guarded runtime
  (:func:`app.extensions.runtime.http.guarded_request`) — SSRF-guarded,
  per-hop revalidated, pinned-address.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.extensions.builtin.withings import oauth
from app.extensions.builtin.withings.mappers import (
    group_created_epoch,
    measuregrp_is_hostile,
    measuregrp_to_observations,
)
from app.extensions.errors import ExtensionError
from app.extensions.sdk import BaseHealthProvider, action_result, kv_block, list_block
from app.extensions.sdk.auth import OAuthStateStore, generate_state
from app.models.enums import IntegrationCredentialKind, IntegrationSyncStream
from app.models.user_integration import UserIntegration
from app.services.integration.integration_credentials import (
    CredentialError,
    read_credential,
    revoke_credential,
    seal_credential,
)

logger = logging.getLogger(__name__)

_MEASURE_CURSOR_KEY = "measure"
_PAGE_SIZE = 100
_MAX_PAGES = 20
#: Refresh when the access token expires within this leeway.
_REFRESH_LEEWAY_SECONDS = 300
_DEFAULT_WINDOW_DAYS = 90


class WithingsProvider(BaseHealthProvider):
    domain = "withings"

    async def setup(self, config: dict) -> None:
        self._system_config = config or {}
        self._state_store = OAuthStateStore()

    # ------------------------------------------------------- credential slot

    def _config(self, integration: UserIntegration) -> dict:
        return integration.user_config or {}

    async def _load_slot(self, integration: UserIntegration) -> tuple[Any, dict[str, Any]]:
        """The ``(row, payload)`` of the sealed ``oauth2`` slot — fail-closed.

        A missing, revoked or undecryptable slot raises
        :class:`CredentialError` (charter ``auth``) before any HTTP call.
        """
        from app.core.database import AsyncSessionLocal
        from app.models.user_integration import IntegrationCredential

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(IntegrationCredential).where(
                    IntegrationCredential.user_integration_id == integration.id,
                    IntegrationCredential.kind == IntegrationCredentialKind.OAUTH2.value,
                )
            )
            row = result.scalars().one_or_none()
            if row is None:
                raise CredentialError(
                    "No oauth2 credential is stored for this instance — authorize Withings first."
                )
            payload = await read_credential(db, integration, IntegrationCredentialKind.OAUTH2)
            return row, payload

    async def _live_token(self, integration: UserIntegration) -> str:
        """A live Withings access token — refreshing BEFORE any pull.

        The sealed payload must carry the client secret (every Withings
        refresh needs it). An expired or near-expiry access token triggers
        a refresh whose response RESEALS the same slot in place: new token
        pair under the current key, ``rotated_at`` stamped,
        ``access_expires_at``/``refresh_expires_at`` restamped, revocation
        cleared. The client secret carries forward untouched. A failed
        refresh raises :class:`CredentialError` — the sync stops before
        any data call.
        """
        row, payload = await self._load_slot(integration)
        now = datetime.now(UTC)
        expires_at = row.access_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and now < expires_at - timedelta(seconds=_REFRESH_LEEWAY_SECONDS):
            token = payload.get("access_token")
            if isinstance(token, str) and token:
                return token

        refresh_token = payload.get("refresh_token")
        client_secret = payload.get("client_secret")
        client_id = self._config(integration).get("withings_client_id")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CredentialError("The sealed credential carries no refresh token — reconnect.")
        if not isinstance(client_secret, str) or not client_secret or not client_id:
            raise CredentialError(
                "The sealed credential carries no usable client secret — reconnect Withings."
            )
        try:
            body = await oauth.refresh_tokens(
                self._http_client, refresh_token, client_id, client_secret
            )
        except ExtensionError as exc:
            raise CredentialError(
                f"Withings refresh failed — reconnect the source ({exc.code})."
            ) from exc
        new_payload, access_expires_at, refresh_expires_at = oauth.normalize_token(body)
        await self._reseal(
            integration,
            {**payload, **new_payload},
            access_expires_at,
            refresh_expires_at,
        )
        return str(new_payload["access_token"])

    async def _reseal(
        self,
        integration: UserIntegration,
        payload: dict[str, Any],
        access_expires_at: datetime,
        refresh_expires_at: datetime,
    ) -> None:
        """Rotate the ``oauth2`` slot in place under the same key."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await seal_credential(
                db,
                integration,
                IntegrationCredentialKind.OAUTH2,
                payload,
                scopes=[payload["scope"]] if isinstance(payload.get("scope"), str) else None,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
            )
            await db.commit()

    # ------------------------------------------------------------- OAuth round-trip

    async def begin_oauth(self, integration, redirect_uri, *, extra_state=None):
        """Start the Withings authorization-code round-trip.

        Returns ``(authorize_url, state)``. Only the non-secret client id
        is needed here; the callback exchanges the code against the sealed
        client secret.
        """
        client_id = (self._config(integration).get("withings_client_id") or "").strip()
        if not client_id:
            raise ExtensionError("auth", "Instance has no withings_client_id configured.")
        state = generate_state()
        payload = {"redirect_uri": redirect_uri, "client_id": client_id}
        if extra_state:
            payload.update(extra_state)
        await self._state_store.issue(state, payload)
        return oauth.build_authorize_url(client_id, redirect_uri, state), state

    async def complete_oauth(self, integration, pending, code) -> dict[str, Any]:
        """Exchange the authorization code and seal the token set.

        The sealed ``oauth2`` slot is rotated in place to carry the full
        credential: client secret (kept), access + refresh tokens, scope —
        with the expiry columns stamped. The Withings user id (the account
        binding) is recorded in ``user_config`` as non-secret metadata.
        """
        if not pending or not pending.get("redirect_uri"):
            raise ExtensionError("auth", "Unknown or expired OAuth state.")
        client_id = (pending.get("client_id") or "").strip()
        if not client_id:
            raise ExtensionError("auth", "OAuth state carries no client id.")
        _row, payload = await self._load_slot(integration)
        client_secret = payload.get("client_secret")
        if not isinstance(client_secret, str) or not client_secret:
            raise CredentialError(
                "The sealed credential carries no client secret — re-save the config form."
            )
        try:
            body = await oauth.exchange_code(
                self._http_client, code, pending["redirect_uri"], client_id, client_secret
            )
        except ExtensionError as exc:
            raise CredentialError(
                f"Withings authorization failed — reconnect the source ({exc.code})."
            ) from exc
        new_payload, access_expires_at, refresh_expires_at = oauth.normalize_token(body)
        await self._reseal(
            integration,
            {**payload, **new_payload},
            access_expires_at,
            refresh_expires_at,
        )

        config = dict(integration.user_config or {})
        if isinstance(body.get("userid"), (str, int)):
            config["withings_userid"] = str(body["userid"])
        if isinstance(new_payload.get("scope"), str):
            config["withings_scope"] = new_payload["scope"]
        integration.user_config = config
        return {"userid": config.get("withings_userid")}

    async def revoke(self, integration) -> None:
        """Disconnect: revoke the sealed ``oauth2`` slot (fail-closed).

        Withings' token-revocation webservice requires its partner HMAC
        signature scheme (nonce + signature), which this connector
        deliberately does not implement — disconnecting revokes the sealed
        slot locally, so the next sync fails closed before any pull, and
        the remote tokens simply expire (access in hours; the refresh
        token becomes invalid on its next rotation).
        """
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await revoke_credential(db, integration, IntegrationCredentialKind.OAUTH2)
            await db.commit()

    # ------------------------------------------------------------- measure pull

    async def pull_data(self, integration: UserIntegration) -> list[Any]:
        """Withings ``measure`` groups → ingest-funnel payloads.

        Refreshes the access token first (fail-closed on a broken
        credential), then pulls the measure category behind its
        ``integration_sync_state`` cursor (the newest group ``created``
        epoch seen; the initial pull covers the configured window). The
        mapped measure types convert to FHIR Observation payloads through
        the mappers — the funnel routes them (telemetry for
        catalog-flagged biomarkers with canonical units and a registered
        device; everything unmapped lands raw with the gap recorded).
        """
        access_token = await self._live_token(integration)
        config = self._config(integration)
        cursor = self._initial_cursor(integration)
        form: dict[str, str] = {"category": "1", "limit": str(_PAGE_SIZE)}
        if cursor is not None:
            form["lastupdate"] = str(cursor)
        else:
            form["startdate"] = str(self._window_start_epoch(config))
            form["enddate"] = str(int(datetime.now(UTC).timestamp()))

        accepted: list[dict[str, Any]] = []
        rejected: dict[str, int] = {}
        latest = cursor or 0
        for _page in range(_MAX_PAGES):
            body = await oauth.fetch_measuregrps(self._http_client, access_token, form)
            groups = body.get("measuregrps")
            if not isinstance(groups, list):
                break
            for grp in groups:
                hostile = measuregrp_is_hostile(grp)
                if hostile is not None:
                    rejected[hostile] = rejected.get(hostile, 0) + 1
                    continue
                observations, counters = measuregrp_to_observations(
                    grp, patient_id=integration.patient_id
                )
                for key, count in counters.items():
                    if count:
                        rejected[key] = rejected.get(key, 0) + count
                accepted.extend(observations)
                created = group_created_epoch(grp)
                if created is not None and created > latest:
                    latest = created
            if not body.get("more"):
                break
            offset = body.get("offset")
            form["offset"] = str(int(offset) if isinstance(offset, (int, float)) else _PAGE_SIZE)

        if latest > (cursor or 0):
            self.set_cursor(
                integration,
                IntegrationSyncStream.TELEMETRY,
                str(latest),
                key=_MEASURE_CURSOR_KEY,
            )
        if rejected:
            await self.log_debug_payload(
                integration,
                "Withings measure pull — rejected measures",
                {"rejected": rejected},
                level="warning",
            )
        await self.log_debug_payload(
            integration,
            "Withings measure pull",
            {
                "accepted": len(accepted),
                "rejected": rejected,
                "cursor_key": _MEASURE_CURSOR_KEY,
                "stream": IntegrationSyncStream.TELEMETRY.value,
                "latest": latest or None,
            },
        )

        from app.extensions.sdk.fhir import fhir_observation_to_create

        out = []
        for fhir_obs in accepted:
            try:
                payload = fhir_observation_to_create(
                    fhir_obs,
                    tenant_id=integration.tenant_id,
                    patient_id=integration.patient_id,
                )
            except Exception as map_err:
                logger.debug("measure map failed for %s: %s", integration.id, map_err)
                payload = None
            if payload is not None:
                out.append(payload)
        return out

    def _initial_cursor(self, integration: UserIntegration) -> int | None:
        """The measure cursor: the saved ``created`` floor, or ``None`` on
        first sync (the window form applies)."""
        saved = self.get_cursor(
            integration, IntegrationSyncStream.TELEMETRY, key=_MEASURE_CURSOR_KEY
        )
        if saved in (None, ""):
            return None
        try:
            return int(saved)
        except (TypeError, ValueError):
            return None

    def _window_start_epoch(self, config: dict) -> int:
        days = config.get("initial_window_days")
        if not isinstance(days, int) or days < 1:
            days = _DEFAULT_WINDOW_DAYS
        return int((datetime.now(UTC) - timedelta(days=days)).timestamp())

    # ------------------------------------------------------------- actions

    def get_custom_actions(self) -> list[dict[str, str]]:
        return [
            {"id": "check_connection", "label": "Check Connection", "style": "default"},
            {"id": "reset_cursors", "label": "Reset Cursors", "style": "warning"},
        ]

    async def execute_custom_action(
        self, integration: UserIntegration, action_id: str, **kwargs
    ) -> dict[str, Any]:
        if action_id == "check_connection":
            return await self._action_check_connection(integration)
        if action_id == "reset_cursors":
            return await self._action_reset_cursors(integration)
        raise NotImplementedError(f"Action '{action_id}' is not implemented by {self.domain}.")

    async def _action_check_connection(self, integration: UserIntegration) -> dict[str, Any]:
        """Validate config → credential → live token → Withings reachability,
        in that order, failing closed on the first broken layer."""
        config = self._config(integration)
        client_id = (config.get("withings_client_id") or "").strip()
        if not client_id:
            return action_result(
                message="Instance has no withings_client_id configured.",
                results=[kv_block("Config", {"client_id": None})],
            )
        try:
            access_token = await self._live_token(integration)
        except CredentialError as e:
            return action_result(
                message="Credential check failed — reconnect Withings.",
                results=[kv_block("Credential", {"error": str(e)})],
            )
        try:
            body = await oauth.fetch_measuregrps(
                self._http_client,
                access_token,
                {
                    "category": "1",
                    "startdate": str(int((datetime.now(UTC) - timedelta(days=1)).timestamp())),
                    "enddate": str(int(datetime.now(UTC).timestamp())),
                    "limit": "1",
                },
            )
        except ExtensionError as e:
            return action_result(
                message="Withings is not reachable (or refused the request).",
                results=[kv_block("Connection", {"error": e.detail})],
            )
        binding = config.get("withings_userid")
        return action_result(
            message="Connection OK — credential accepted and Withings answered.",
            results=[
                kv_block(
                    "Connection",
                    {
                        "client_id": client_id,
                        "withings_user": binding or "unknown",
                        "groups_in_window": len(body.get("measuregrps") or []),
                    },
                ),
                list_block(
                    "Pulls",
                    [
                        "measure (weight, body composition, blood pressure, "
                        "heart rate, SpO2, temperature)"
                    ],
                ),
            ],
        )

    async def _action_reset_cursors(self, integration: UserIntegration) -> dict[str, Any]:
        from app.services.integration.integration_sync_state import clear_all

        cleared = clear_all(integration)
        return action_result(
            message="Cursors reset — the next sync re-pulls the full window.",
            results=[kv_block("Cleared", {"cursors": cleared})],
        )

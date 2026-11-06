"""FHIR Client connector — the pull-only inbound FHIR R4 client (plan 8 W4).

The read-side twin of ``fhir_server``: it connects to an **external** FHIR
R4 server and pulls the declared resource classes into the ingest funnel —
Observation (biomarker/clinical events), DiagnosticReport (labs),
MedicationStatement (medications) and Encounter (appointments). There is
no push leg: local data leaves through ``fhir_server`` or the facade, never
through this connector.

Auth is credential-table-first: the bearer/API-key lives in
``integration_credentials`` (kind ``api_key``, sealed, context-bound) —
never in ``user_config``. Every pull is fail-closed: a missing, revoked or
unreadable credential raises the typed :class:`CredentialError` before any
search runs, so revocation stops data flow instead of running
unauthenticated (the S7 gate, enforced again at the provider edge because
this connector has no tokenless-when-keyed fallback). SMART-on-FHIR oauth2
is deliberately NOT wired here (the hospital two-way connector already
speaks SMART; flagging the gap beats half-wiring a second OAuth stack).

Cursors are per-resource rows in ``integration_sync_state`` (``_lastUpdated``
floor per resource class, stream per data class); all writes go through
``services/integration_ingest.py`` via the sync engine. Hostile resources
(oversized, wrong-typed) are rejected with typed counters before mapping.
"""

from __future__ import annotations

import datetime as dt
import logging
from datetime import datetime, timedelta
from typing import Any

from app.extensions.builtin.fhir_client.mappers import (
    diagnostic_report_to_payload,
    encounter_to_examination,
    medication_statement_to_create,
    observations_from_bundle_entry,
    patient_summary,
    resource_is_hostile,
)
from app.extensions.errors import ExtensionError
from app.extensions.sdk import (
    BaseHealthProvider,
    action_result,
    fhir_search,
    kv_block,
    list_block,
)
from app.models.enums import IntegrationSyncStream
from app.models.user_integration import UserIntegration
from app.services.integration.integration_credentials import CredentialError, read_credential

logger = logging.getLogger(__name__)

_STREAM_BY_RESOURCE: dict[str, IntegrationSyncStream] = {
    "Observation": IntegrationSyncStream.OBSERVATIONS,
    "DiagnosticReport": IntegrationSyncStream.LABS,
    "MedicationStatement": IntegrationSyncStream.OBSERVATIONS,
    "Encounter": IntegrationSyncStream.APPOINTMENTS,
}

_PAGE_SIZE = 50
_AUTH_MODES = ("api_key", "none")
_DEFAULT_WINDOW_MONTHS = 12


class FhirClientProvider(BaseHealthProvider):
    domain = "fhir_client"

    async def setup(self, config: dict) -> None:
        self._system_config = config or {}

    # ------------------------------------------------------- config / auth

    def _config(self, integration: UserIntegration) -> dict:
        return integration.user_config or {}

    async def _access_token(self, integration: UserIntegration) -> str | None:
        """The bearer token for the remote server — fail-closed.

        ``api_key`` mode reads the sealed credential slot: a missing or
        revoked slot raises :class:`CredentialError` (charter ``auth``)
        before any HTTP call is made. ``none`` mode returns ``None``
        (tokenless servers). Any other configured mode is a config error.
        """
        auth_mode = self._config(integration).get("auth_mode", "api_key")
        if auth_mode not in _AUTH_MODES:
            raise ExtensionError(
                "invalid", f"Unsupported auth_mode {auth_mode!r} for this connector."
            )
        if auth_mode == "none":
            return None
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            payload = await read_credential(db, integration, "api_key")
        api_key = payload.get("api_key")
        if not api_key or not isinstance(api_key, str):
            raise CredentialError("The stored api_key credential carries no usable token.")
        return api_key

    def _remote_patient(self, integration: UserIntegration) -> str:
        """The patient binding — required, never pulled unscoped."""
        remote = (self._config(integration).get("remote_patient_id") or "").strip()
        if not remote:
            raise ExtensionError(
                "invalid",
                "Instance has no remote_patient_id — this connector never pulls unscoped.",
            )
        return remote

    # ------------------------------------------------------------ search

    async def _search_resource(
        self,
        integration: UserIntegration,
        resource_type: str,
        *,
        extra_params: dict[str, str] | None = None,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Bounded ``_lastUpdated`` delta search with a per-resource cursor.

        The cursor floor is the saved ``integration_sync_state`` row for
        ``(stream(resource), resource)`` — one slow resource never starves
        its siblings — or ``now - time_window_months`` on first sync. The
        cursor advances past the newest ``meta.lastUpdated`` seen; the
        boundary flushes it in its transaction.
        """
        config = self._config(integration)
        fhir_base_url = (config.get("fhir_base_url") or "").rstrip("/")
        if not fhir_base_url:
            return []

        token = await self._access_token(integration)
        cursor = self._initial_cursor(integration, resource_type)
        params: dict[str, str] = {
            "_sort": "_lastUpdated",
            "_count": str(_PAGE_SIZE),
            "_lastUpdated": f"gt{cursor}",
            "patient": self._remote_patient(integration),
        }
        if extra_params:
            params.update(extra_params)

        resources = await fhir_search(
            self._http_client,
            fhir_base_url,
            resource_type,
            params,
            access_token=token,
            max_pages=max_pages,
        )

        accepted: list[dict[str, Any]] = []
        rejected: dict[str, int] = {}
        latest = cursor
        for resource in resources:
            hostile = resource_is_hostile(resource)
            if hostile is not None:
                rejected[hostile] = rejected.get(hostile, 0) + 1
                continue
            if resource.get("resourceType") != resource_type:
                rejected["wrong_resource"] = rejected.get("wrong_resource", 0) + 1
                continue
            updated = (resource.get("meta") or {}).get("lastUpdated")
            if isinstance(updated, str) and updated > latest:
                latest = updated
            accepted.append(resource)

        if latest > cursor:
            self.set_cursor(
                integration,
                _STREAM_BY_RESOURCE[resource_type],
                latest,
                key=resource_type,
            )
        if rejected:
            await self.log_debug_payload(
                integration,
                f"FHIR search {resource_type} — hostile resources rejected",
                {"rejected": rejected},
                level="warning",
            )
        await self.log_debug_payload(
            integration,
            f"FHIR search {resource_type}",
            {
                "accepted": len(accepted),
                "rejected": rejected,
                "cursor_key": resource_type,
                "stream": _STREAM_BY_RESOURCE[resource_type].value,
                "latest": latest,
            },
        )
        return accepted

    def _initial_cursor(self, integration: UserIntegration, resource_type: str) -> str:
        """The ``_lastUpdated`` floor: saved cursor, else now - time window."""
        stream = _STREAM_BY_RESOURCE[resource_type]
        saved = self.get_cursor(integration, stream, key=resource_type)
        if saved:
            return str(saved)
        months = int(self._config(integration).get("time_window_months") or _DEFAULT_WINDOW_MONTHS)
        cutoff = datetime.now(dt.UTC) - timedelta(days=30 * months)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -------------------------------------------------------- pull hooks

    async def pull_data(self, integration: UserIntegration) -> list[Any]:
        """Observation → the observations stream (biomarker engine input).

        Typed errors (``auth`` for a missing/revoked credential, ``protocol``,
        ``unreachable``) propagate — the engine routes them (the instance
        flips to ERROR with the credential class) instead of the pull
        silently producing zero data forever.
        """
        resources = await self._search_resource(integration, "Observation")
        tenant_id = integration.tenant_id
        patient_id = integration.patient_id
        out = []
        for resource in resources:
            try:
                payload = observations_from_bundle_entry(
                    resource, tenant_id=tenant_id, patient_id=patient_id
                )
            except Exception as map_err:
                logger.debug("observation map failed for %s: %s", integration.id, map_err)
                payload = None
            if payload is not None:
                out.append(payload)
        return out

    async def pull_labs(self, integration: UserIntegration) -> list[dict[str, Any]]:
        """DiagnosticReport → the labs stream (ingest-funnel payload dicts)."""
        resources = await self._search_resource(integration, "DiagnosticReport")
        out = []
        for resource in resources:
            payload = diagnostic_report_to_payload(resource, patient_id=integration.patient_id)
            if payload is not None:
                out.append(payload)
        return out

    async def pull_medications(self, integration: UserIntegration) -> list[Any]:
        """MedicationStatement → the medication records (engine writes)."""
        resources = await self._search_resource(integration, "MedicationStatement")
        out = []
        for resource in resources:
            try:
                record = medication_statement_to_create(resource, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("medication map failed for %s: %s", integration.id, map_err)
                record = None
            if record is not None:
                out.append(record)
        return out

    async def pull_examinations(self, integration: UserIntegration) -> list[Any]:
        """Encounter → the appointments stream (examinations)."""
        resources = await self._search_resource(integration, "Encounter")
        out = []
        for resource in resources:
            try:
                exam = encounter_to_examination(resource, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("encounter map failed for %s: %s", integration.id, map_err)
                exam = None
            if exam is not None:
                out.append(exam)
        return out

    # ----------------------------------------------------- custom actions

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
        """Validate base URL + credential + the bound remote Patient — in
        that order, failing closed on the first broken layer."""
        config = self._config(integration)
        fhir_base_url = (config.get("fhir_base_url") or "").rstrip("/")
        if not fhir_base_url:
            return {"message": "Instance has no fhir_base_url configured."}
        try:
            token = await self._access_token(integration)
        except CredentialError as e:
            return action_result(
                message="Credential check failed — reconnect the source.",
                results=[kv_block("Credential", {"error": str(e)})],
            )
        remote_patient = self._remote_patient(integration)
        try:
            resources = await fhir_search(
                self._http_client,
                fhir_base_url,
                "Patient",
                {"_id": remote_patient, "_count": "1"},
                access_token=token,
                max_pages=1,
            )
        except ExtensionError as e:
            return action_result(
                message="The FHIR server is not reachable (or refused the request).",
                results=[kv_block("Connection", {"url": fhir_base_url, "error": e.detail})],
            )
        patient = patient_summary(resources[0]) if resources else None
        if patient is None or patient["id"] != remote_patient:
            return action_result(
                message="The bound remote Patient was not found on the server.",
                results=[
                    kv_block(
                        "Patient binding",
                        {"remote_patient_id": remote_patient, "found": False},
                    )
                ],
            )
        return action_result(
            message="Connection OK — credential accepted and patient binding resolved.",
            results=[
                kv_block(
                    "Connection",
                    {
                        "url": fhir_base_url,
                        "auth_mode": config.get("auth_mode", "api_key"),
                        "patient": f"{patient['name']} ({patient['id']})",
                    },
                ),
                list_block(
                    "Pulled resources",
                    ["Observation", "DiagnosticReport", "MedicationStatement", "Encounter"],
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

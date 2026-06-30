"""FHIR Server integration provider (Stage 2 pull + Stage 2b push).

Pull (Stage 2): bounded FHIR search of remote ``Observation`` resources within
the configured window/categories, converted to ``ObservationCreate`` attached to
the local patient and returned for the engine's biomarker mapping + telemetry
routing. SMART standalone-launch (Pair A) or tokenless mode.

Push (Stage 2b): local Observations → external FHIR server via **conditional
update** (``PUT /Observation?identifier=…``). A stable per-local-UUID identifier
makes the upsert idempotent; the subject is rewritten to the remote patient; the
server-assigned ``id``/``meta.versionId`` are dropped so the server owns them.
``412 Precondition Failed`` is treated as "skipped" (no change needed).
Observations sourced from *this* integration are excluded (no pull→push echo),
and only LOINC/SNOMED-coded observations are pushed (custom biomarkers have no
hospital terminology).

``sync_direction`` gates the *automatic* sync (background + platform Sync Now):
``both`` (default), ``pull_only``, ``push_only``, ``none``. The custom actions
(``pull_now`` / ``push_now``) bypass it for explicit manual control.

Every step logs structured payloads via :meth:`log_debug_payload` — toggle the
instance's Debug Mode to inspect URLs, params, status codes, per-resource
decisions, and HTTP headers (Authorization redacted) in the frontend Debug
Console.
"""
import datetime as dt
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.models.user_integration import UserIntegration
from app.schemas.fhir.observation import ObservationCreate
from integrations.sdk import (
    BaseHealthProvider,
    SmartOAuth,
    action_result,
    fhir_conditional_update,
    fhir_observation_to_create,
    fhir_search,
    kv_block,
    list_block,
    table_block,
    text_block,
)
from integrations.sdk.exceptions import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationError,
)

from app.models.enums import CodingSystem

logger = logging.getLogger(__name__)

_CATEGORY_FILTER = {"laboratory": "laboratory", "vital-signs": "vital-signs"}
_PAGE_SIZE = 100
_OBS_IDENTIFIER_SYSTEM = "urn:healthassistant:observation"
_STANDARD_SYSTEMS = {CodingSystem.LOINC.fhir_system, CodingSystem.SNOMED.fhir_system}
_DIRECTION_DEFAULT = "both"
_AUTO_PULL_DISABLED = {"push_only", "none"}
_AUTO_PUSH_DISABLED = {"pull_only", "none"}
_PUSH_BATCH_LIMIT = 500


class FhirServerProvider(BaseHealthProvider):
    domain = "fhir_server"

    async def setup(self, config: dict) -> None:
        self._smart = SmartOAuth(self._http_client)

    # ------------------------------------------------------------------ OAuth

    async def begin_oauth(self, integration, redirect_uri, *, extra_state=None):
        fhir_base_url = (integration.user_config or {}).get("fhir_base_url")
        if not fhir_base_url:
            raise IntegrationAuthError("Instance has no fhir_base_url configured.")
        # H1: request write scopes when push is enabled so the SMART consent
        # screen includes patient/*.write. The user must re-authorize if the
        # sync_direction was changed to push after initial authorization.
        push_enabled = self._direction(integration) in ("both", "push_only")
        return await self._smart.begin_connect(
            fhir_base_url, redirect_uri, "Health Assistant",
            push_enabled=push_enabled, extra_state=extra_state,
        )

    async def complete_oauth(self, integration, pending, code):
        return await self._smart.complete_connect(integration, pending, code)

    async def get_live_token(self, integration: UserIntegration) -> str:
        return await self._smart.get_live_token(integration)

    async def revoke(self, integration: UserIntegration) -> None:
        """Best-effort token revocation (RFC 7009) — delegates to SmartOAuth."""
        await self._smart.revoke(integration)

    # ---------------------------------------------------- config / resolution

    def _config(self, integration: UserIntegration) -> dict:
        return integration.user_config or {}

    def _direction(self, integration: UserIntegration) -> str:
        return self._config(integration).get("sync_direction", _DIRECTION_DEFAULT)

    def _remote_patient(self, integration: UserIntegration) -> Optional[str]:
        config = self._config(integration)
        if config.get("auth_mode", "smart") == "smart":
            try:
                return self._smart.tokens.get_patient(integration)
            except Exception:
                return None
        return config.get("remote_patient_id")

    # -------------------------------------------------------------- pull (PULL)

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        """Engine hook. No-op when auto-pull is disabled by ``sync_direction``."""
        direction = self._direction(integration)
        if direction in _AUTO_PULL_DISABLED:
            await self.log_debug_payload(
                integration, "Pull skipped (sync_direction)", {"sync_direction": direction}
            )
            return []
        try:
            return await self._run_pull(integration, persist=False)
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.error("fhir_server %s pull failed: %s", integration.id, e)
            await self.log_debug_payload(
                integration, "Pull error", {"error": str(e)}, level="error"
            )
            return []

    async def _run_pull(
        self, integration: UserIntegration, *, persist: bool
    ) -> List[ObservationCreate]:
        config = self._config(integration)
        auth_mode = config.get("auth_mode", "smart")
        fhir_base_url = config.get("fhir_base_url")
        if not fhir_base_url:
            return []
        if auth_mode == "smart" and not config.get("_oauth"):
            return []  # PENDING (not yet authorized)

        time_window_months = int(config.get("time_window_months") or 12)
        category_choice = config.get("categories") or "both"
        cursor = self._initial_cursor(integration, time_window_months)
        remote_patient = self._remote_patient(integration)

        params: Dict[str, str] = {
            "_sort": "_lastUpdated",
            "_count": str(_PAGE_SIZE),
            "_lastUpdated": f"gt{cursor}",
        }
        if remote_patient:
            params["patient"] = remote_patient
        if category_choice in _CATEGORY_FILTER:
            params["category"] = _CATEGORY_FILTER[category_choice]

        await self.log_debug_payload(
            integration,
            "FHIR pull search",
            {"url": f"{fhir_base_url}/Observation", "params": params, "auth_mode": auth_mode},
        )

        try:
            if auth_mode == "smart":
                resources = await self._authorized_search(
                    integration, fhir_base_url, "Observation", params
                )
            else:
                resources = await fhir_search(
                    self._http_client, fhir_base_url, "Observation", params, max_pages=50
                )
        except (IntegrationAuthError, IntegrationDataError) as e:
            await self.log_debug_payload(
                integration,
                "FHIR pull search failed",
                {"error": str(e), "params": params},
                level="error",
            )
            raise

        observations: List[ObservationCreate] = []
        latest = cursor
        skipped = 0
        for fhir_obs in resources:
            if fhir_obs.get("resourceType") != "Observation":
                skipped += 1
                continue
            created = fhir_observation_to_create(
                fhir_obs,
                tenant_id=integration.tenant_id,
                patient_id=integration.patient_id,
            )
            if created is not None:
                observations.append(created)
            else:
                skipped += 1
            updated = fhir_obs.get("meta", {}).get("lastUpdated")
            if updated and updated > latest:
                latest = updated

        if latest > cursor:
            self.set_sync_cursor(integration, "last_updated", latest)

        await self.log_debug_payload(
            integration,
            f"FHIR pull -> {len(observations)} mapped ({skipped} skipped)",
            {
                "mapped": len(observations),
                "skipped": skipped,
                "latest": latest,
                "persist": persist,
            },
        )

        if persist and observations:
            counts = await self._persist_observations(integration, observations)
            await self.log_debug_payload(
                integration, "FHIR pull persisted", counts
            )
        return observations

    async def _persist_observations(
        self, integration: UserIntegration, observations: List[ObservationCreate]
    ) -> Dict[str, int]:
        """Persist pulled observations in the provider's own session.

        Mirrors the background ``sync_active_integrations`` task: map to
        biomarkers, stamp an Integration performer, add all as FHIR observations.
        Used by the ``pull_now`` action so it doesn't depend on the request
        endpoint's pipeline.
        """
        from app.core.database import AsyncSessionLocal
        from app.models.fhir import Observation
        from app.services.fhir_service import map_observations_to_biomarkers

        orm_obs = []
        for obs_data in observations:
            obs_dict = obs_data.model_dump(exclude_unset=True)
            orm_obs.append(Observation(**obs_dict))

        async with AsyncSessionLocal() as db:
            await map_observations_to_biomarkers(db, orm_obs)
            for obs in orm_obs:
                if not obs.performer:
                    obs.performer = [
                        {
                            "type": "Integration",
                            "display": integration.instance_name or integration.provider,
                            "reference": f"Integration/{integration.id}",
                        }
                    ]
                db.add(obs)
            await db.commit()
        return {"fhir": len(orm_obs), "telemetry": 0}

    async def _authorized_search(
        self, integration: UserIntegration, base_url: str, resource_type: str, params: dict
    ) -> list:
        """SMART search: get a live token, refresh once on a 401 race."""
        token = await self._smart.get_live_token(integration)
        try:
            return await fhir_search(
                self._http_client, base_url, resource_type, params,
                access_token=token, max_pages=50,
            )
        except IntegrationAuthError:
            logger.info("401 on %s search; force-refreshing token and retrying once.", resource_type)
            await self.log_debug_payload(
                integration,
                "Token 401 race — force-refreshing",
                {"resource_type": resource_type},
            )
            token = await self._smart.force_refresh(integration)
            return await fhir_search(
                self._http_client, base_url, resource_type, params,
                access_token=token, max_pages=50,
            )

    def _initial_cursor(self, integration: UserIntegration, time_window_months: int) -> str:
        """The ``_lastUpdated`` floor: saved cursor, else now - time_window."""
        saved = self.get_sync_cursor(integration, "last_updated")
        if saved:
            return str(saved)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * time_window_months)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -------------------------------------------------------------- push (PUSH)

    async def push_data(self, integration: UserIntegration, data: Any) -> None:
        """Engine hook. No-op when auto-push is disabled by ``sync_direction``."""
        direction = self._direction(integration)
        if direction in _AUTO_PUSH_DISABLED:
            return
        try:
            await self._run_push(integration)
        except IntegrationAuthError:
            raise  # let the engine pause the integration (re-auth needed)
        except Exception as e:
            logger.error("fhir_server %s push failed: %s", integration.id, e)
            await self.log_debug_payload(
                integration, "Push error", {"error": str(e)}, level="error"
            )

    async def _run_push(self, integration: UserIntegration) -> Dict[str, Any]:
        config = self._config(integration)
        fhir_base_url = config.get("fhir_base_url")
        if not fhir_base_url:
            return _empty_push_result()
        auth_mode = config.get("auth_mode", "smart")
        if auth_mode == "smart" and not config.get("_oauth"):
            return _empty_push_result()  # PENDING

        remote_patient = self._remote_patient(integration)
        now = dt.datetime.now(dt.timezone.utc)
        since = self._push_since(integration, now)

        candidates = await self._load_push_candidates(integration, since)
        pushable, excluded_echo, excluded_coding = self._filter_push_candidates(
            integration, candidates
        )

        await self.log_debug_payload(
            integration,
            "FHIR push candidates",
            {
                "candidates": len(candidates),
                "pushable": len(pushable),
                "excluded_echo": excluded_echo,
                "excluded_coding": excluded_coding,
                "since": _iso(since),
                "remote_patient": remote_patient,
            },
        )

        created = updated = skipped = errors = 0
        insufficient_scope = False
        prov_counters: Dict[str, int] = {}
        device_id = await self._resolve_device_id(integration)
        max_pushed_at = None  # tracks the latest updated_at among successful rows
        for obs in pushable:
            outcome = await self._push_one(
                integration, fhir_base_url, auth_mode, remote_patient, obs,
                device_id=device_id, prov_counters=prov_counters,
            )
            if outcome == "created":
                created += 1
                if obs.updated_at and (max_pushed_at is None or obs.updated_at > max_pushed_at):
                    max_pushed_at = obs.updated_at
            elif outcome == "updated":
                updated += 1
                if obs.updated_at and (max_pushed_at is None or obs.updated_at > max_pushed_at):
                    max_pushed_at = obs.updated_at
            elif outcome == "skipped":
                skipped += 1
            elif outcome == "insufficient_scope":
                insufficient_scope = True
                errors += 1
                break
            else:
                errors += 1

        result: Dict[str, Any] = {
            "pushed": created + updated,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "candidates": len(pushable),
            "at": now.isoformat(),
            "provenance_created": prov_counters.get("provenance_created", 0),
            "provenance_failed": prov_counters.get("provenance_failed", 0),
        }
        if insufficient_scope:
            result["warning"] = (
                "Push stopped — the authorization token lacks write scope "
                "(patient/*.write). Re-authorize the integration to request "
                "write permissions (the SMART consent screen will appear)."
            )
        # Push resilience: only advance the cursor past successfully-pushed
        # rows. If ALL rows failed, the cursor stays unchanged → full retry
        # next cycle (was: advanced to `now` unconditionally → failed rows
        # were never retried → silent data loss on transient failures).
        if max_pushed_at is not None:
            self.set_sync_cursor(integration, "last_pushed_at", max_pushed_at.isoformat())
        self.set_sync_cursor(integration, "last_push_result", result)
        await self.log_debug_payload(
            integration,
            f"FHIR push -> {result['pushed']} sent "
            f"(created={created}, updated={updated}, skipped={skipped}, errors={errors})",
            result,
        )
        return result

    async def _push_one(
        self, integration, fhir_base_url, auth_mode, remote_patient, obs,
        *, device_id=None, prov_counters=None,
    ) -> str:
        """Push a single Observation. Returns ``created``/``updated``/``skipped``/``error``.

        H3: after a successful PUT, POSTs a Provenance to the remote server
        (best-effort — never aborts the push). ``prov_counters`` (a mutable
        dict) is incremented for tracking.
        """
        local_id = str(obs.id)
        try:
            body = obs.to_fhir_dict()
        except Exception as e:
            logger.warning("fhir_server %s push skip (invalid FHIR) %s: %s", integration.id, local_id, e)
            await self.log_debug_payload(
                integration,
                "Push skip — invalid FHIR projection",
                {"observation_id": local_id, "error": str(e)},
                level="warning",
            )
            return "error"

        body = dict(body)
        if remote_patient:
            body["subject"] = {"reference": f"Patient/{remote_patient}"}
        body["identifier"] = _with_identifier(body.get("identifier"), local_id)
        body.pop("id", None)  # let the server assign its own id
        meta = dict(body.get("meta") or {})
        meta.pop("versionId", None)  # server controls versioning
        body["meta"] = meta

        search_params = {"identifier": f"{_OBS_IDENTIFIER_SYSTEM}|{local_id}"}
        try:
            token = (
                await self._smart.get_live_token(integration) if auth_mode == "smart" else None
            )
        except IntegrationAuthError:
            raise

        try:
            status, _resp = await fhir_conditional_update(
                self._http_client, fhir_base_url, "Observation", body,
                search_params=search_params, access_token=token,
            )
        except IntegrationAuthError as e:
            # H1: detect 403 insufficient_scope — the token lacks write
            # permissions. Surface an actionable signal.
            if "insufficient_scope" in str(e).lower() or (
                "scope" in str(e).lower() and "403" in str(e)
            ):
                return "insufficient_scope"
            # Push resilience: 401-race retry. The token was valid when
            # get_live_token checked, but expired between the check and the
            # PUT (a race). Force-refresh and retry once — mirrors the pull
            # path's _authorized_search pattern. If it still fails, count
            # this row as an error and continue the batch (don't abort).
            try:
                token = await self._smart.force_refresh(integration)
            except IntegrationAuthError:
                return "error"
            try:
                status, _resp = await fhir_conditional_update(
                    self._http_client, fhir_base_url, "Observation", body,
                    search_params=search_params, access_token=token,
                )
            except IntegrationError as retry_err:
                logger.warning(
                    "fhir_server %s push still failing after token refresh for %s: %s",
                    integration.id, local_id, retry_err,
                )
                return "error"
        except IntegrationError as e:
            logger.warning("fhir_server %s push failed for %s: %s", integration.id, local_id, e)
            await self.log_debug_payload(
                integration,
                "Push failed",
                {"observation_id": local_id, "error": str(e), "url": f"{fhir_base_url}/Observation"},
                level="warning",
            )
            return "error"

        await self.log_debug_payload(
            integration,
            "Push result",
            {"observation_id": local_id, "status": status, "identifier": search_params["identifier"]},
        )
        if status == 412:
            return "skipped"

        # H3: POST a Provenance to the remote server after a successful push
        # (hospitals require this for regulatory audit). Best-effort — a
        # Provenance failure (404/405 = server doesn't support Provenance,
        # network error, etc.) is logged and never aborts the push.
        remote_id = (_resp or {}).get("id") if isinstance(_resp, dict) else None
        if remote_id and prov_counters is not None:
            await self._post_remote_provenance(
                integration, fhir_base_url, auth_mode, remote_id, device_id, prov_counters,
            )

        if status == 201:
            return "created"
        return "updated"

    async def _post_remote_provenance(
        self, integration, fhir_base_url, auth_mode, remote_obs_id, device_id, counters,
    ):
        """H3: POST a Provenance resource for the just-pushed Observation."""
        from integrations.sdk.fhir import fhir_create
        from integrations.sdk.exceptions import IntegrationError

        instance_name = (integration.user_config or {}).get("instance_name") or integration.provider
        agent_who = {"reference": f"Device/{device_id}"} if device_id else {"display": "Health Assistant"}
        prov_body = {
            "resourceType": "Provenance",
            "target": [{"reference": f"Observation/{remote_obs_id}"}],
            "recorded": datetime.now(timezone.utc).isoformat(),
            "activity": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3/ProvenanceEventType", "code": "CREATE"}]},
            "agent": [{
                "who": agent_who,
                "onBehalfOf": {"display": f"Health Assistant (integration: {instance_name})"},
            }],
        }
        try:
            token = await self._smart.get_live_token(integration) if auth_mode == "smart" else None
            await fhir_create(
                self._http_client, fhir_base_url, "Provenance", prov_body,
                access_token=token,
            )
            counters["provenance_created"] = counters.get("provenance_created", 0) + 1
        except IntegrationError as e:
            logger.debug("Remote Provenance POST failed for %s: %s", integration.id, e)
            counters["provenance_failed"] = counters.get("provenance_failed", 0) + 1
        except Exception:
            counters["provenance_failed"] = counters.get("provenance_failed", 0) + 1

    async def _resolve_device_id(self, integration) -> Optional[str]:
        """H3: resolve the DeviceModel id for this integration (for Provenance agent.who).

        Mirrors ``provenance_service._resolve_device_ref`` — looks up
        ``DeviceModel.owner_integration_id == integration.id``. Returns the
        device id as a string, or None if no Device row exists.
        """
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.fhir.device import DeviceModel
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    select(DeviceModel.id).where(
                        DeviceModel.owner_integration_id == integration.id
                    )
                )
                row = res.first()
                return str(row[0]) if row else None
        except Exception:
            return None

    async def _load_push_candidates(self, integration, since) -> list:
        from app.core.database import AsyncSessionLocal
        from app.models.fhir.patient import Observation
        from sqlalchemy import select

        patient_ref = f"Patient/{integration.patient_id}"
        async with AsyncSessionLocal() as db:
            stmt = (
                select(Observation)
                .where(
                    Observation.tenant_id == integration.tenant_id,
                    Observation.subject["reference"].astext == patient_ref,
                    Observation.updated_at > since,
                )
                .order_by(Observation.updated_at.asc())
                .limit(_PUSH_BATCH_LIMIT)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    def _filter_push_candidates(self, integration, candidates):
        """Drop echo (sourced from this integration) and non-standard coding."""
        integ_ref = f"Integration/{integration.id}"
        domain = integration.provider
        pushable, echo, coding = [], 0, 0
        for obs in candidates:
            if _sourced_from_this_integration(obs.performer, integ_ref, domain):
                echo += 1
                continue
            if not _has_standard_coding(obs.code):
                coding += 1
                continue
            pushable.append(obs)
        return pushable, echo, coding

    def _push_since(self, integration, now):
        """Push cursor: ``last_pushed_at`` else now - time_window_months."""
        parsed = _parse_iso(self.get_sync_cursor(integration, "last_pushed_at"))
        if parsed:
            return parsed
        months = int(self._config(integration).get("time_window_months") or 12)
        return now - dt.timedelta(days=30 * months)

    # ------------------------------------------------------- push dry-run

    async def _push_preview(self, integration) -> Dict[str, Any]:
        """Compute push candidates without sending anything."""
        if not self._config(integration).get("fhir_base_url"):
            return {"candidates": [], "excluded_echo": 0, "excluded_coding": 0, "since": "—"}
        now = dt.datetime.now(dt.timezone.utc)
        since = self._push_since(integration, now)
        candidates = await self._load_push_candidates(integration, since)
        pushable, echo, coding = self._filter_push_candidates(integration, candidates)
        rows = []
        for obs in pushable:
            rows.append(
                {
                    "id": str(obs.id),
                    "code": _code_display(obs.code),
                    "value": _observation_value_display(obs),
                    "updated": obs.updated_at.isoformat() if obs.updated_at else None,
                }
            )
        await self.log_debug_payload(
            integration,
            "Push preview (dry-run)",
            {"pushable": len(rows), "excluded_echo": echo, "excluded_coding": coding},
        )
        return {
            "candidates": rows,
            "excluded_echo": echo,
            "excluded_coding": coding,
            "since": _iso(since),
        }

    # ---------------------------------------------------- check connectivity

    async def _check_connection(self, integration) -> Dict[str, Any]:
        """GET {base}/metadata and summarize the CapabilityStatement.

        Validates that the server is reachable and (for SMART mode) that the
        stored token still authenticates. Returns a dict with ``ok`` plus either
        connection details or an ``error``.
        """
        config = self._config(integration)
        fhir_base_url = config.get("fhir_base_url")
        if not fhir_base_url:
            return {"ok": False, "error": "No fhir_base_url configured."}
        auth_mode = config.get("auth_mode", "smart")
        url = f"{fhir_base_url.rstrip('/')}/metadata"
        req_headers: Dict[str, str] = {}
        if auth_mode == "smart":
            if not config.get("_oauth"):
                return {
                    "ok": False,
                    "error": "Instance is PENDING — authorize first.",
                    "auth_mode": auth_mode,
                    "url": url,
                }
            try:
                token = await self._smart.get_live_token(integration)
            except IntegrationAuthError as e:
                await self.log_debug_payload(
                    integration,
                    "Check connection — token refresh failed",
                    {"error": str(e), "url": url},
                    level="error",
                )
                return {"ok": False, "error": str(e), "auth_mode": auth_mode, "url": url}
            req_headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self._http_client.get(url, headers=req_headers or None)
        except Exception as e:
            await self.log_debug_payload(
                integration,
                "Check connection — network error",
                {"url": url, "error": str(e)},
                level="error",
            )
            return {"ok": False, "error": f"Network error: {e}", "url": url}

        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None

        await self.log_debug_payload(
            integration,
            "Check connection — metadata response",
            {
                "url": url,
                "status": status,
                "auth_mode": auth_mode,
                "headers_sent": _redact(req_headers),
            },
        )

        if status >= 400:
            return {
                "ok": False,
                "error": f"Server returned HTTP {status}",
                "status": status,
                "url": url,
                "auth_mode": auth_mode,
            }
        cap = (
            body
            if isinstance(body, dict) and body.get("resourceType") == "CapabilityStatement"
            else None
        )
        info: Dict[str, Any] = {
            "ok": True,
            "url": url,
            "status": status,
            "auth_mode": auth_mode,
            "remote_patient": self._remote_patient(integration),
        }
        if cap:
            info["fhir_version"] = cap.get("fhirVersion")
            software = cap.get("software") or {}
            info["software"] = software.get("name") or "—"
            info["software_version"] = software.get("version") or "—"
            rest = cap.get("rest") or []
            resource_types = []
            if rest and isinstance(rest[0], dict):
                for r in rest[0].get("resource") or []:
                    if isinstance(r, dict) and r.get("type"):
                        resource_types.append(r["type"])
            info["resources"] = sorted(set(resource_types))
        return info

    # ------------------------------------------------------------- custom actions

    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "check_connection", "label": "Check Connection", "style": "default"},
            {"id": "pull_now", "label": "Pull Now", "style": "primary"},
            {"id": "push_now", "label": "Push Now", "style": "primary"},
            {"id": "push_preview", "label": "Push Preview", "style": "default"},
            {"id": "reset_cursors", "label": "Reset Cursors", "style": "warning"},
        ]

    async def execute_custom_action(
        self, integration: UserIntegration, action_id: str, **kwargs
    ) -> Dict[str, Any]:
        if action_id == "check_connection":
            return await self._action_check_connection(integration)
        if action_id == "pull_now":
            return await self._action_pull_now(integration)
        if action_id == "push_now":
            return await self._action_push_now(integration)
        if action_id == "push_preview":
            return await self._action_push_preview(integration)
        if action_id == "reset_cursors":
            return await self._action_reset_cursors(integration)
        raise NotImplementedError(f"Action '{action_id}' is not implemented by {self.domain}.")

    async def _action_check_connection(self, integration) -> Dict[str, Any]:
        info = await self._check_connection(integration)
        if not info.get("ok"):
            return action_result(
                message=f"Connection check failed: {info.get('error')}",
                results=[
                    kv_block(
                        "Details",
                        {k: v for k, v in info.items() if k != "error" and v is not None},
                    )
                ],
            )
        summary = {
            "Server": info.get("url"),
            "Status": f"HTTP {info.get('status')}",
            "Auth mode": info.get("auth_mode"),
            "Remote patient": info.get("remote_patient") or "—",
            "FHIR version": info.get("fhir_version", "—"),
            "Software": f"{info.get('software', '—')} {info.get('software_version', '')}".strip(),
        }
        blocks = [kv_block("Connection", summary)]
        if info.get("resources"):
            blocks.append(list_block("Supported resources", info["resources"]))
        return action_result(message="Connection OK.", results=blocks)

    async def _action_pull_now(self, integration) -> Dict[str, Any]:
        if not self._config(integration).get("fhir_base_url"):
            return {"message": "Instance has no fhir_base_url configured."}
        try:
            observations = await self._run_pull(integration, persist=True)
        except IntegrationAuthError as e:
            return {"message": f"Pull failed (auth): {e}"}
        except IntegrationDataError as e:
            return {"message": f"Pull failed: {e}"}
        cursor = self.get_sync_cursor(integration, "last_updated") or "—"
        return action_result(
            message=f"Pulled and stored {len(observations)} observation(s).",
            results=[
                kv_block(
                    "Pull result",
                    {
                        "Mapped": len(observations),
                        "New cursor": cursor,
                        "Remote patient": self._remote_patient(integration) or "—",
                    },
                )
            ],
        )

    async def _action_push_now(self, integration) -> Dict[str, Any]:
        if not self._config(integration).get("fhir_base_url"):
            return {"message": "Instance has no fhir_base_url configured."}
        try:
            result = await self._run_push(integration)
        except IntegrationAuthError as e:
            return {"message": f"Push failed (auth): {e}"}
        return action_result(
            message=f"Pushed {result['pushed']} observation(s) to the FHIR server.",
            results=[
                kv_block(
                    "Push result",
                    {
                        "Created": result["created"],
                        "Updated": result["updated"],
                        "Skipped (412)": result["skipped"],
                        "Errors": result["errors"],
                        "At": result["at"],
                    },
                )
            ],
        )

    async def _action_push_preview(self, integration) -> Dict[str, Any]:
        preview = await self._push_preview(integration)
        rows = preview["candidates"]
        blocks = [
            kv_block(
                "Summary",
                {
                    "Pushable": len(rows),
                    "Excluded (echo from this integration)": preview["excluded_echo"],
                    "Excluded (non-standard coding)": preview["excluded_coding"],
                    "Since": preview["since"],
                },
            )
        ]
        if rows:
            blocks.append(
                table_block(
                    "Candidates",
                    ["Code", "Value", "Updated"],
                    [[r["code"], r["value"], r["updated"]] for r in rows[:50]],
                )
            )
        else:
            blocks.append(text_block("Candidates", "Nothing to push."))
        return action_result(
            message=f"{len(rows)} observation(s) would be pushed.", results=blocks
        )

    async def _action_reset_cursors(self, integration) -> Dict[str, Any]:
        new_config = dict(integration.user_config or {})
        new_state = dict(new_config.get("_sync_state") or {})
        cleared = [
            key for key in ("last_updated", "last_pushed_at", "last_push_result") if key in new_state
        ]
        for key in cleared:
            new_state.pop(key, None)
        new_config["_sync_state"] = new_state
        integration.user_config = new_config
        await self.log_debug_payload(integration, "Cursors reset", {"cleared": cleared})
        return action_result(
            message=f"Reset {len(cleared)} cursor(s). Next sync re-pulls/re-pushes the full window.",
            results=[
                kv_block(
                    "Cleared",
                    {k: "yes" for k in cleared} if cleared else {"none": "—"},
                )
            ],
        )


# ----------------------------------------------------------------- helpers


def _empty_push_result() -> Dict[str, Any]:
    return {
        "pushed": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "candidates": 0,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _has_standard_coding(code: Any) -> bool:
    """True if the Observation code carries a LOINC/SNOMED coding."""
    if not isinstance(code, dict):
        return False
    for c in code.get("coding") or []:
        if isinstance(c, dict) and c.get("system") in _STANDARD_SYSTEMS:
            return True
    return False


def _sourced_from_this_integration(performer: Any, integ_ref: str, domain: str) -> bool:
    """True if the observation's performer points at this integration.

    Matches the explicit ``Integration/{id}`` reference (endpoint + pull_now
    path) OR a display equal to the provider domain (background-sync path,
    which stores only ``display = integration.provider``).
    """
    if not isinstance(performer, list):
        return False
    for p in performer:
        if not isinstance(p, dict):
            continue
        if p.get("reference") == integ_ref:
            return True
        if domain and p.get("display") == domain:
            return True
    return False


def _with_identifier(existing: Any, local_id: str) -> List[Dict[str, str]]:
    """Stamp the local-UUID identifier (idempotent — replaces any prior HA one)."""
    if isinstance(existing, list):
        ident = [i for i in existing if isinstance(i, dict)]
    elif isinstance(existing, dict):
        ident = [existing]
    else:
        ident = []
    ident = [i for i in ident if i.get("system") != _OBS_IDENTIFIER_SYSTEM]
    ident.append({"system": _OBS_IDENTIFIER_SYSTEM, "value": local_id})
    return ident


def _parse_iso(value: Any):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(d) -> str:
    return d.isoformat() if d else "—"


def _code_display(code: Any) -> str:
    if not isinstance(code, dict):
        return "—"
    if code.get("text"):
        return str(code["text"])
    for c in code.get("coding") or []:
        if isinstance(c, dict) and c.get("code"):
            display = c.get("display")
            return f"{c['code']} ({display})" if display else str(c["code"])
    return "—"


def _observation_value_display(obs) -> str:
    vq = getattr(obs, "value_quantity", None)
    if isinstance(vq, dict):
        v = vq.get("value")
        u = vq.get("unit") or vq.get("code") or ""
        return f"{v} {u}".strip()
    if getattr(obs, "value_string", None):
        return str(obs.value_string)
    cc = getattr(obs, "value_codeableConcept", None)
    if isinstance(cc, dict):
        return cc.get("text") or _code_display(cc) or "—"
    if getattr(obs, "raw_value", None) is not None:
        return str(obs.raw_value)
    return "—"


def _redact(headers: Dict[str, str]) -> Dict[str, str]:
    """Redact Authorization values for safe debug logging."""
    out = dict(headers or {})
    for k in list(out):
        if k.lower() == "authorization":
            out[k] = "Bearer ***"
    return out

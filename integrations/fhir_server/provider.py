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
    DocumentPull,
    SmartOAuth,
    action_result,
    biomarker_hitl_proposal,
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
from integrations.fhir_server.mappers import (
    allergy_intolerance_to_create,
    condition_to_event,
    document_reference_meta,
    encounter_to_exam,
    immunization_to_create,
    medication_request_to_record,
    medication_statement_to_record,
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

# Multi-resource pull (Phases 1–4 of the fhir-server multi-resource sync
# plan). The config key ``pull_resources`` (a list of tokens, or ``"all"``)
# selects which resource types an instance pulls. Tokens are deliberately
# coarse-grained — a single "Medication" token covers both FHIR
# MedicationStatement and MedicationRequest (both map to the same HA
# ``fhir_medications`` row via the ``intent`` discriminator).
_ALL_RESOURCES = "all"
_RESOURCE_TOKENS = (
    "Condition",          # → clinical events
    "Encounter",          # → examinations
    "DocumentReference",  # → documents + OCR
    "Medication",         # → MedicationStatement + MedicationRequest
    "AllergyIntolerance", # → allergies
    "Immunization",       # → immunizations
)


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
        """Resolve the remote FHIR patient id for this instance.

        An explicit ``remote_patient_id`` in the config wins (set by the
        Find Patient picker or manual entry) — it lets the operator
        override the SMART-resolved patient or supply one for tokenless
        servers. Falls back to the SMART launch token's patient for
        ``smart`` mode; ``none`` mode with no explicit id returns None
        (pulls unscoped — usually wrong, but the server's call).
        """
        config = self._config(integration)
        explicit = config.get("remote_patient_id")
        if explicit:
            return explicit
        if config.get("auth_mode", "smart") == "smart":
            try:
                return self._smart.tokens.get_patient(integration)
            except Exception:
                return None
        return None

    # --------------------------------------------------- remote patient picker

    async def _search_remote_patients(
        self,
        integration: UserIntegration,
        *,
        query: Optional[str] = None,
        identifier: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Search the remote FHIR server for Patient resources.

        ``identifier`` searches by MRN/identifier (most precise);
        ``query`` searches by name. Returns a flat list of summarized
        patient dicts (see :meth:`_summarize_patient`). Degrades to
        ``[]`` on auth/network failure so the picker UI never crashes.
        """
        config = self._config(integration)
        auth_mode = config.get("auth_mode", "smart")
        fhir_base_url = config.get("fhir_base_url")
        if not fhir_base_url:
            return []
        if auth_mode == "smart" and not config.get("_oauth"):
            return []  # PENDING — not authorized yet

        params: Dict[str, str] = {"_count": str(limit)}
        if identifier:
            params["identifier"] = identifier
        elif query:
            params["name"] = query

        try:
            if auth_mode == "smart":
                resources = await self._authorized_search(
                    integration, fhir_base_url, "Patient", params, max_pages=1
                )
            else:
                resources = await fhir_search(
                    self._http_client, fhir_base_url, "Patient", params, max_pages=1
                )
        except (IntegrationAuthError, IntegrationDataError) as e:
            await self.log_debug_payload(
                integration, "Patient search failed",
                {"error": str(e), "params": params}, level="warning",
            )
            return []

        return [
            self._summarize_patient(r)
            for r in resources
            if isinstance(r, dict) and r.get("resourceType") == "Patient"
        ]

    @staticmethod
    def _summarize_patient(fhir_patient: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce a FHIR Patient to the picker-relevant fields."""
        def _name() -> str:
            names = fhir_patient.get("name") or []
            if isinstance(names, dict):
                names = [names]
            for n in names:
                if not isinstance(n, dict):
                    continue
                if n.get("text"):
                    return str(n["text"])
                given = n.get("given") or []
                family = n.get("family") or ""
                if isinstance(given, list):
                    given = " ".join(given)
                full = f"{given} {family}".strip()
                if full:
                    return full
            return "—"

        def _mrn() -> Optional[str]:
            for ident in fhir_patient.get("identifier") or []:
                if isinstance(ident, dict) and ident.get("value"):
                    sys = str(ident.get("system") or "").lower()
                    if "mrn" in sys or not ident.get("type"):
                        return str(ident["value"])
            # fallback: first identifier value of any kind
            for ident in fhir_patient.get("identifier") or []:
                if isinstance(ident, dict) and ident.get("value"):
                    return str(ident["value"])
            return None

        return {
            "id": str(fhir_patient.get("id") or ""),
            "name": _name(),
            "mrn": _mrn(),
            "birth_date": fhir_patient.get("birthDate"),
            "gender": fhir_patient.get("gender"),
        }

    async def _local_patient_hint(self, integration: UserIntegration) -> Dict[str, Any]:
        """Load the local patient's MRN + name to seed auto-suggest.

        Returns ``{"mrn": str|None, "name": str|None}``. Never raises —
        a lookup failure just means no auto-suggest.
        """
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.fhir.patient import Patient
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        select(Patient.mrn, Patient.name).where(
                            Patient.id == integration.patient_id
                        )
                    )
                ).first()
            if not row:
                return {"mrn": None, "name": None}
            mrn = row[0]
            raw_name = row[1]
            name: Optional[str] = None
            if isinstance(raw_name, dict):
                given = raw_name.get("given") or []
                if isinstance(given, list):
                    given = " ".join(given)
                name = f"{given} {raw_name.get('family') or ''}".strip() or raw_name.get("text")
            elif isinstance(raw_name, list) and raw_name and isinstance(raw_name[0], dict):
                n0 = raw_name[0]
                given = n0.get("given") or []
                if isinstance(given, list):
                    given = " ".join(given)
                name = f"{given} {n0.get('family') or ''}".strip() or n0.get("text")
            return {"mrn": str(mrn) if mrn else None, "name": name}
        except Exception:
            return {"mrn": None, "name": None}

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
        """Pull remote Observations and (optionally) persist them locally.

        Delegates the bounded FHIR search + per-resource cursor to the
        generic :meth:`_search_resource`; the Observation-specific work
        here is the FHIR→``ObservationCreate`` mapping and the optional
        direct persist (used by the ``pull_now`` action).
        """
        config = self._config(integration)
        auth_mode = config.get("auth_mode", "smart")
        if auth_mode == "smart" and not config.get("_oauth"):
            return []  # PENDING (not yet authorized)
        if not config.get("fhir_base_url"):
            return []

        category_choice = config.get("categories") or "both"
        extra_params: Optional[Dict[str, str]] = None
        if category_choice in _CATEGORY_FILTER:
            extra_params = {"category": _CATEGORY_FILTER[category_choice]}

        try:
            resources = await self._search_resource(
                integration,
                "Observation",
                extra_params=extra_params,
                cursor_key="last_updated",
            )
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.error("fhir_server %s pull failed: %s", integration.id, e)
            await self.log_debug_payload(
                integration, "Pull error", {"error": str(e)}, level="error"
            )
            return []

        observations: List[ObservationCreate] = []
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

        await self.log_debug_payload(
            integration,
            f"FHIR pull -> {len(observations)} mapped ({skipped} skipped)",
            {"mapped": len(observations), "skipped": skipped, "persist": persist},
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
        self, integration: UserIntegration, base_url: str, resource_type: str,
        params: dict, *, max_pages: int = 50,
    ) -> list:
        """SMART search: get a live token, refresh once on a 401 race."""
        token = await self._smart.get_live_token(integration)
        try:
            return await fhir_search(
                self._http_client, base_url, resource_type, params,
                access_token=token, max_pages=max_pages,
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
                access_token=token, max_pages=max_pages,
            )

    async def _search_resource(
        self,
        integration: UserIntegration,
        resource_type: str,
        *,
        extra_params: Optional[Dict[str, str]] = None,
        cursor_key: Optional[str] = None,
        max_pages: int = 50,
    ) -> List[Dict[str, Any]]:
        """Generic bounded FHIR search with a per-resource ``_lastUpdated`` cursor.

        The multi-resource counterpart to the Observation-specific search
        that used to live inline in ``_run_pull``. Used by every
        ``pull_*`` hook (Conditions, Encounters, DocumentReference,
        MedicationStatement/Request, AllergyIntolerance, Immunization).

        - Builds ``_sort=_lastUpdated`` + ``_count`` + ``_lastUpdated=gt<cursor>``
          + ``patient=<remote_patient>`` (when known) + any ``extra_params``.
        - SMART mode: token-aware search with a single 401-race retry
          (delegates to :meth:`_authorized_search`). ``none`` mode:
          tokenless. Returns ``[]`` early for PENDING (smart, no token
          yet) or unconfigured instances.
        - Advances a per-resource cursor keyed ``cursor_key`` (defaults
          to ``f"last_updated:{resource_type}"``) so each resource type
          has its own delta window and a single slow resource can't
          starve the others.

        Returns the flat list of resource dicts (the Bundle entries are
        unwrapped by :func:`fhir_search`). Raises
        ``IntegrationAuthError`` / ``IntegrationDataError`` on failure —
        callers wrap those to return ``[]`` (hook contract: never raise).
        """
        config = self._config(integration)
        auth_mode = config.get("auth_mode", "smart")
        fhir_base_url = config.get("fhir_base_url")
        if not fhir_base_url:
            return []
        if auth_mode == "smart" and not config.get("_oauth"):
            return []  # PENDING (not yet authorized)

        effective_cursor_key = (
            cursor_key if cursor_key is not None
            else f"last_updated:{resource_type}"
        )
        time_window_months = int(config.get("time_window_months") or 12)
        cursor = self._initial_cursor(
            integration, time_window_months, effective_cursor_key
        )
        remote_patient = self._remote_patient(integration)

        params: Dict[str, str] = {
            "_sort": "_lastUpdated",
            "_count": str(_PAGE_SIZE),
            "_lastUpdated": f"gt{cursor}",
        }
        if remote_patient:
            params["patient"] = remote_patient
        if extra_params:
            params.update(extra_params)

        await self.log_debug_payload(
            integration,
            f"FHIR search {resource_type}",
            {
                "url": f"{fhir_base_url}/{resource_type}",
                "params": params,
                "auth_mode": auth_mode,
                "cursor_key": effective_cursor_key,
            },
        )

        try:
            if auth_mode == "smart":
                resources = await self._authorized_search(
                    integration, fhir_base_url, resource_type, params,
                    max_pages=max_pages,
                )
            else:
                resources = await fhir_search(
                    self._http_client, fhir_base_url, resource_type, params,
                    max_pages=max_pages,
                )
        except (IntegrationAuthError, IntegrationDataError) as e:
            await self.log_debug_payload(
                integration,
                f"FHIR search {resource_type} failed",
                {"error": str(e), "params": params},
                level="error",
            )
            raise

        # Advance the per-resource cursor past the newest row we saw.
        latest = cursor
        for r in resources:
            updated = (r.get("meta") or {}).get("lastUpdated")
            if updated and updated > latest:
                latest = updated
        if latest > cursor:
            self.set_sync_cursor(integration, effective_cursor_key, latest)

        await self.log_debug_payload(
            integration,
            f"FHIR search {resource_type} -> {len(resources)} resource(s)",
            {"count": len(resources), "latest": latest},
        )
        return resources

    async def _fetch_attachment(
        self, integration: UserIntegration, url: str, *, access_token: Optional[str] = None
    ) -> bytes:
        """Fetch a DocumentReference attachment as raw bytes.

        Handles three URL shapes a remote FHIR server may use:

        - Absolute ``http(s)://...`` — fetched directly.
        - ``<base>/Binary/{id}`` (relative path) — resolved against the
          configured ``fhir_base_url``.
        - ``urn:ha-document:<id>`` — the HA-internal scheme; not produced
          by remote servers, so treated as unreachable (returns ``b""``).

        SMART-mode attachments are fetched with the live bearer token
        (best-effort refresh on 401). Failures return ``b""`` so the
        caller can skip the document rather than abort the whole pull —
        a single unreachable attachment must not break the sync.
        """
        if not url:
            return b""
        config = self._config(integration)
        fhir_base_url = (config.get("fhir_base_url") or "").rstrip("/")
        auth_mode = config.get("auth_mode", "smart")

        if url.startswith("urn:ha-document:"):
            # Internal scheme — remote servers never use it; nothing to fetch.
            return b""

        if url.lower().startswith(("http://", "https://")):
            fetch_url = url
        else:
            fetch_url = f"{fhir_base_url}/{url.lstrip('/')}"

        headers: Dict[str, str] = {"Accept": "application/fhir+json, application/octet-stream, application/json"}
        if auth_mode == "smart":
            try:
                token = access_token or await self._smart.get_live_token(integration)
                headers["Authorization"] = f"Bearer {token}"
            except IntegrationAuthError:
                pass

        try:
            response = await self._http_client.get(fetch_url, headers=headers)
        except Exception as e:
            await self.log_debug_payload(
                integration, "Attachment fetch failed (network)",
                {"url": fetch_url, "error": str(e)}, level="warning",
            )
            return b""
        if response.status_code == 401 and auth_mode == "smart":
            # 401 race — force-refresh and retry once.
            try:
                token = await self._smart.force_refresh(integration)
                headers["Authorization"] = f"Bearer {token}"
                response = await self._http_client.get(fetch_url, headers=headers)
            except Exception as e:
                await self.log_debug_payload(
                    integration, "Attachment fetch failed after refresh",
                    {"url": fetch_url, "error": str(e)}, level="warning",
                )
                return b""
        if response.status_code >= 400:
            await self.log_debug_payload(
                integration, "Attachment fetch failed (HTTP)",
                {"url": fetch_url, "status": response.status_code}, level="warning",
            )
            return b""
        return response.content

    def _initial_cursor(
        self, integration: UserIntegration, time_window_months: int, key: str = "last_updated"
    ) -> str:
        """The ``_lastUpdated`` floor: saved cursor, else now - time_window."""
        saved = self.get_sync_cursor(integration, key)
        if saved:
            return str(saved)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * time_window_months)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --------------------------------------------------- multi-resource pull
    # Phases 1–4 of the fhir-server multi-resource sync plan
    # (dev/plans/fhir-server-multi-resource-sync-2026-07-23.md). Every hook
    # below mirrors the established supports_X / pull_X opt-in shape. Each
    # ``pull_*`` honours ``sync_direction`` (no-op when auto-pull is
    # disabled, like ``pull_data``) and the per-instance ``pull_resources``
    # selection so operators can opt a specific instance out of, say,
    # allergy ingest without disabling the whole integration.

    def _resource_enabled(self, integration: UserIntegration, token: str) -> bool:
        """Is ``token`` in this instance's ``pull_resources`` selection?"""
        selected = self._config(integration).get("pull_resources")
        if not selected or selected == _ALL_RESOURCES:
            return True
        if isinstance(selected, list):
            return token in selected or _ALL_RESOURCES in selected
        return False

    def _can_pull(self, integration: UserIntegration, token: str) -> bool:
        """Gate: sync_direction allows pulling AND the resource is selected."""
        if self._direction(integration) in _AUTO_PULL_DISABLED:
            return False
        return self._resource_enabled(integration, token)

    # ---- Phase 1: clinical events (Condition) ----

    def supports_clinical_events(self) -> bool:
        return True

    async def pull_clinical_events(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "Condition"):
            return []
        try:
            resources = await self._search_resource(integration, "Condition")
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s conditions pull failed: %s", integration.id, e)
            return []
        out = []
        for res in resources:
            try:
                ev = condition_to_event(res, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("condition map failed for %s: %s", integration.id, map_err)
                ev = None
            if ev is not None:
                out.append(ev)
        await self.log_debug_payload(
            integration, "FHIR Condition -> events", {"mapped": len(out), "raw": len(resources)},
        )
        return out

    # ---- Phase 1: examinations (Encounter) ----

    def supports_examinations(self) -> bool:
        return True

    async def pull_examinations(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "Encounter"):
            return []
        try:
            resources = await self._search_resource(integration, "Encounter")
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s encounters pull failed: %s", integration.id, e)
            return []
        out = []
        for res in resources:
            try:
                exam = encounter_to_exam(res, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("encounter map failed for %s: %s", integration.id, map_err)
                exam = None
            if exam is not None:
                out.append(exam)
        await self.log_debug_payload(
            integration, "FHIR Encounter -> exams", {"mapped": len(out), "raw": len(resources)},
        )
        return out

    # ---- Phase 2: documents (DocumentReference) ----

    def supports_documents(self) -> bool:
        return True

    async def pull_documents(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "DocumentReference"):
            return []
        try:
            resources = await self._search_resource(integration, "DocumentReference")
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s docrefs pull failed: %s", integration.id, e)
            return []

        # Resolve a live token once (SMART mode) so repeated attachment
        # fetches reuse it instead of re-refreshing per file.
        token: Optional[str] = None
        if self._config(integration).get("auth_mode", "smart") == "smart":
            try:
                token = await self._smart.get_live_token(integration)
            except IntegrationAuthError:
                token = None

        out: List[DocumentPull] = []
        for res in resources:
            try:
                meta = document_reference_meta(res)
            except Exception as map_err:
                logger.debug("docref map failed for %s: %s", integration.id, map_err)
                continue
            if meta is None:
                continue
            for att in meta.attachments:
                try:
                    content = await self._fetch_attachment(
                        integration, att.get("url") or "", access_token=token
                    )
                except Exception as fetch_err:
                    logger.debug("attachment fetch failed for %s: %s", integration.id, fetch_err)
                    content = b""
                if not content:
                    # An unreachable attachment shouldn't abort the pull;
                    # the engine's byte cap + per-doc handling already
                    # tolerate empty content gracefully.
                    continue
                out.append(
                    DocumentPull(
                        filename=att.get("filename") or "document",
                        content=content,
                        content_type=att.get("content_type"),
                        examination_external_id=meta.examination_external_id,
                        category_concept_slug=meta.category_concept_slug,
                        external_id=meta.external_id,
                        include_in_extraction=True,
                    )
                )
        await self.log_debug_payload(
            integration, "FHIR DocumentReference -> pulls", {"mapped": len(out), "raw": len(resources)},
        )
        return out

    # ---- Phase 3: HITL catalog proposals (unmapped LOINC/SNOMED codes) ----

    def supports_hitl_proposals(self) -> bool:
        return True

    async def pull_hitl_proposals(self, integration: UserIntegration) -> List[Any]:
        """Propose biomarker definitions for remote codes the local catalog lacks.

        Scans recently-arrived remote Observations (using its own cursor so
        it doesn't fight the main pull), collects LOINC/SNOMED codes, and
        for any code absent from the local ``BiomarkerDefinition`` catalog
        AND not already proposed (the ``hitl:seen_codes`` cursor) emits a
        ``biomarker_hitl_proposal`` carrying the code, display, and unit
        gleaned from the remote resource. Re-syncs are no-ops for already-
        seen codes; ``handle_proposal_resolution`` suppresses re-proposal
        of resolved codes regardless of outcome.
        """
        # Gated on sync_direction only (not pull_resources) — this scans
        # the core Observation stream, which is always pulled when the
        # direction allows it.
        if self._direction(integration) in _AUTO_PULL_DISABLED:
            return []
        # Only propose from standard-coded observations — custom biomarkers
        # have no hospital terminology worth contributing.
        try:
            resources = await self._search_resource(
                integration, "Observation",
                cursor_key="hitl:codes_scanned",
            )
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s hitl scan failed: %s", integration.id, e)
            return []

        # code_key -> {display, unit, category, system}
        observed: Dict[str, Dict[str, Any]] = {}
        for res in resources:
            code = res.get("code") if isinstance(res, dict) else None
            if not isinstance(code, dict):
                continue
            for coding in code.get("coding") or []:
                if not isinstance(coding, dict):
                    continue
                system = coding.get("system") or ""
                if "loinc.org" not in system and "snomed" not in system:
                    continue
                c = coding.get("code")
                if not c:
                    continue
                observed[str(c)] = {
                    "display": coding.get("display") or code.get("text") or str(c),
                    "system": "loinc" if "loinc" in system else "snomed",
                    "unit": None,
                    "category": None,
                }
            vq = res.get("valueQuantity") if isinstance(res, dict) else None
            if isinstance(vq, dict):
                # Attach the unit to the most-recently-seen code (best-effort).
                for info in reversed(observed.values()):
                    if info.get("unit") is None:
                        info["unit"] = vq.get("unit") or vq.get("code")

        if not observed:
            return []

        known_codes = await self._known_biomarker_codes(integration)
        seen_codes = set(self.get_sync_cursor(integration, "hitl:seen_codes", default=[]) or [])

        proposals = []
        newly_seen = list(seen_codes)
        for code, info in observed.items():
            if code in known_codes or code in seen_codes:
                continue
            proposals.append(
                biomarker_hitl_proposal(
                    title=f"Define Biomarker: {info['display']}",
                    name=info["display"],
                    coding_system=info["system"],
                    code=code,
                    preferred_unit_symbol=info.get("unit"),
                    context={
                        "source": "fhir_server",
                        "remote_code": code,
                        "remote_display": info["display"],
                    },
                )
            )
            newly_seen.append(code)

        # Record seen codes (known + newly proposed) so future syncs skip
        # them even if the proposal is still pending.
        if newly_seen != list(seen_codes):
            self.set_sync_cursor(integration, "hitl:seen_codes", newly_seen)

        await self.log_debug_payload(
            integration, "FHIR HITL proposals",
            {"observed": len(observed), "unknown": len(proposals), "known": len(known_codes)},
        )
        return proposals

    async def handle_proposal_resolution(
        self, integration: UserIntegration, proposal_id, outcome
    ) -> None:
        """Suppress re-proposal of a resolved code regardless of outcome.

        The biomarker ``proposed_payload`` carries the remote code; we add
        it to the ``hitl:seen_codes`` cursor so the next scan treats it as
        resolved (approved → it's now in the catalog anyway; rejected →
        the user explicitly declined, don't nag).
        """
        try:
            payload = getattr(outcome, "final_payload", {}) or {}
            code = payload.get("code")
            if not code:
                return
            seen = set(self.get_sync_cursor(integration, "hitl:seen_codes", default=[]) or [])
            seen.add(str(code))
            self.set_sync_cursor(integration, "hitl:seen_codes", list(seen))
        except Exception as e:
            logger.debug("handle_proposal_resolution failed for %s: %s", integration.id, e)

    async def _known_biomarker_codes(self, integration: UserIntegration) -> set:
        """Load the set of LOINC/SNOMED codes already in the local catalog."""
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.biomarker_model import BiomarkerDefinition
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(BiomarkerDefinition.code).where(
                        BiomarkerDefinition.tenant_id == integration.tenant_id,
                        BiomarkerDefinition.code.isnot(None),
                        BiomarkerDefinition.coding_system.in_(
                            [CodingSystem.LOINC, CodingSystem.SNOMED]
                        ),
                    )
                )
                return {str(row[0]) for row in result.all() if row[0]}
        except Exception:
            return set()

    # ---- Phase 4: medications (MedicationStatement + MedicationRequest) ----

    def supports_medications(self) -> bool:
        return True

    async def pull_medications(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "Medication"):
            return []
        out = []
        for rtype, mapper in (
            ("MedicationStatement", medication_statement_to_record),
            ("MedicationRequest", medication_request_to_record),
        ):
            try:
                resources = await self._search_resource(integration, rtype)
            except (IntegrationAuthError, IntegrationDataError) as e:
                logger.warning("fhir_server %s %s pull failed: %s", integration.id, rtype, e)
                continue
            for res in resources:
                try:
                    rec = mapper(res, patient_id=integration.patient_id)
                except Exception as map_err:
                    logger.debug("%s map failed for %s: %s", rtype, integration.id, map_err)
                    rec = None
                if rec is not None:
                    out.append(rec)
        await self.log_debug_payload(
            integration, "FHIR Medications -> records", {"mapped": len(out)},
        )
        return out

    # ---- Phase 4: allergies (AllergyIntolerance) ----

    def supports_allergies(self) -> bool:
        return True

    async def pull_allergies(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "AllergyIntolerance"):
            return []
        try:
            resources = await self._search_resource(integration, "AllergyIntolerance")
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s allergies pull failed: %s", integration.id, e)
            return []
        out = []
        for res in resources:
            try:
                rec = allergy_intolerance_to_create(res, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("allergy map failed for %s: %s", integration.id, map_err)
                rec = None
            if rec is not None:
                out.append(rec)
        await self.log_debug_payload(
            integration, "FHIR AllergyIntolerance -> records", {"mapped": len(out), "raw": len(resources)},
        )
        return out

    # ---- Phase 4: immunizations (Immunization) ----

    def supports_immunizations(self) -> bool:
        return True

    async def pull_immunizations(self, integration: UserIntegration) -> List[Any]:
        if not self._can_pull(integration, "Immunization"):
            return []
        try:
            resources = await self._search_resource(integration, "Immunization")
        except (IntegrationAuthError, IntegrationDataError) as e:
            logger.warning("fhir_server %s immunizations pull failed: %s", integration.id, e)
            return []
        out = []
        for res in resources:
            try:
                rec = immunization_to_create(res, patient_id=integration.patient_id)
            except Exception as map_err:
                logger.debug("immunization map failed for %s: %s", integration.id, map_err)
                rec = None
            if rec is not None:
                out.append(rec)
        await self.log_debug_payload(
            integration, "FHIR Immunization -> records", {"mapped": len(out), "raw": len(resources)},
        )
        return out

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
            {"id": "find_patient", "label": "Find Patient", "style": "primary",
             "modal": "patient_picker"},
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
        if action_id == "find_patient":
            return await self._action_find_patient(integration, **kwargs)
        if action_id == "select_patient":
            return await self._action_select_patient(integration, **kwargs)
        if action_id == "pull_now":
            return await self._action_pull_now(integration)
        if action_id == "push_now":
            return await self._action_push_now(integration)
        if action_id == "push_preview":
            return await self._action_push_preview(integration)
        if action_id == "reset_cursors":
            return await self._action_reset_cursors(integration)
        raise NotImplementedError(f"Action '{action_id}' is not implemented by {self.domain}.")

    async def _action_find_patient(
        self, integration: UserIntegration, *, query: Optional[str] = None,
        identifier: Optional[str] = None, **_extra,
    ) -> Dict[str, Any]:
        """Search the remote server for a patient; auto-suggests by local MRN.

        Called by the patient-picker modal (``query`` from the search
        box). When called with no query (modal just opened), seeds the
        search from the local patient's MRN, falling back to their name —
        so the most likely match surfaces automatically. Returns a dict
        the picker consumes directly: ``{query, auto_suggested, matches}``.
        """
        auto_suggested = False
        if not query and not identifier:
            hint = await self._local_patient_hint(integration)
            if hint.get("mrn"):
                identifier = hint["mrn"]
                auto_suggested = "MRN"
            elif hint.get("name"):
                query = hint["name"]
                auto_suggested = "name"

        matches = await self._search_remote_patients(
            integration, query=query, identifier=identifier
        )
        await self.log_debug_payload(
            integration, "Find Patient",
            {"query": query, "identifier": identifier, "auto_suggested": auto_suggested,
             "matches": len(matches)},
        )
        return {
            "query": query,
            "identifier": identifier,
            "auto_suggested": auto_suggested or False,
            "matches": matches,
            "current": self._remote_patient(integration),
        }

    async def _action_select_patient(
        self, integration: UserIntegration, *, patient_id: Optional[str] = None,
        **_extra,
    ) -> Dict[str, Any]:
        """Set ``remote_patient_id`` on the instance (the picker's select step)."""
        if not patient_id:
            return {"message": "No patient selected."}
        new_config = dict(integration.user_config or {})
        new_config["remote_patient_id"] = str(patient_id)
        integration.user_config = new_config
        await self.log_debug_payload(
            integration, "Selected remote patient", {"remote_patient_id": patient_id},
        )
        return action_result(
            message=f"Linked remote patient {patient_id}.",
            results=[kv_block("Remote patient", {"id": patient_id})],
        )

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
        """Reset every sync cursor (Observation, per-resource pull, push, HITL).

        Clears the whole ``_sync_state`` dict so the next sync re-pulls /
        re-pushes the full configured window for every resource type. The
        per-resource cursors (``last_updated:Condition``, ...) and the
        HITL seen-codes set (``hitl:seen_codes``) are cleared too — a
        reset is an operator's "start over" switch.
        """
        new_config = dict(integration.user_config or {})
        new_state = dict(new_config.get("_sync_state") or {})
        cleared = sorted(new_state.keys())
        new_config["_sync_state"] = {}
        integration.user_config = new_config
        await self.log_debug_payload(integration, "Cursors reset", {"cleared": cleared})
        return action_result(
            message=f"Reset {len(cleared)} cursor(s). Next sync re-pulls/re-pushes the full window.",
            results=[
                list_block("Cleared", cleared) if cleared else kv_block("Cleared", {"none": "—"}),
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

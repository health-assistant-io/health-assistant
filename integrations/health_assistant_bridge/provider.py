import datetime
import logging
from typing import Any
from uuid import UUID

from integrations.sdk import BaseHealthProvider
from integrations.sdk.observation_builder import ObservationBuilder
from pydantic import BaseModel, Field

from app.ai.schemas.nlp import MetricMappingRequest
from app.models.user_integration import UserIntegration
from app.schemas.fhir.observation import ObservationCreate

logger = logging.getLogger(__name__)

# Per-request document upload byte cap for the bridge's /documents path.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _flatten_reference_range(raw: Any) -> dict[str, float] | None:
    """Normalize a stored ``reference_range`` to the flat ``{low, high}`` shape.

    The bridge push writes the flat shape, but FHIR-imported / OCR / REST rows
    store the FHIR list shape ``[{low: {value, unit}, high: {...}}, ...]``.
    Returns None when nothing meaningful remains.
    """
    if isinstance(raw, list):
        if not raw:
            return None
        raw = raw[0]
    if not isinstance(raw, dict):
        return None

    low = raw.get("low")
    high = raw.get("high")
    if isinstance(low, dict):
        low = low.get("value")
    if isinstance(high, dict):
        high = high.get("value")

    out: dict[str, float] = {}
    if low is not None:
        try:
            out["low"] = float(low)
        except (TypeError, ValueError):
            pass
    if high is not None:
        try:
            out["high"] = float(high)
        except (TypeError, ValueError):
            pass
    return out or None


def _relative_score(
    value: float | None, low: float | None, high: float | None
) -> float | None:
    """Relative position of a value within its reference range (0.0-1.0).

    Mirrors ``ObservationBuilder.build()``: (value - low) / (high - low),
    clamped to [0.0, 1.0]; 0.5 when the range is incomplete (missing bound or
    high <= low). None for non-numeric values.
    """
    if value is None:
        return None
    if low is None or high is None or high <= low:
        return 0.5
    score = (value - low) / (high - low)
    return max(0.0, min(1.0, score))


def _observation_point_from_telemetry(
    row: dict[str, Any], b_def: Any, patient_id: UUID
) -> dict[str, Any]:
    """Map a telemetry row + its BiomarkerDefinition to the ObservationPoint shape.

    The bridge read contract must not branch on source (FHIR vs telemetry), so
    the synthesized row mirrors ``Observation.to_dict()`` field-for-field, with
    the same flat ``reference_range`` normalization applied everywhere.
    """
    from app.models.enums import CodingSystem

    value = row.get("value")
    unit = row.get("unit")
    code = getattr(b_def, "code", None)

    system = getattr(b_def, "coding_system", None)
    if isinstance(system, str):
        system = (
            CodingSystem(system)
            if system in CodingSystem._value2member_map_
            else CodingSystem.CUSTOM
        )
    if system is None:
        system = CodingSystem.CUSTOM
    system_url = getattr(system, "fhir_system", None)

    vt = getattr(b_def, "value_type", None)
    if hasattr(vt, "value"):
        vt = vt.value

    return {
        "id": str(row.get("id")),
        "tenant_id": str(row.get("tenant_id")) if row.get("tenant_id") else None,
        "status": "final",
        "category": None,
        "code": {"coding": [{"system": system_url, "code": code}], "text": b_def.name},
        "subject": {"reference": f"Patient/{patient_id}"},
        "effective_datetime": row.get("timestamp"),
        "value_quantity": {"value": value, "unit": unit} if value is not None else None,
        "value_string": None,
        "value_codeable_concept": None,
        "reference_range": _flatten_reference_range(
            {"low": b_def.reference_range_min, "high": b_def.reference_range_max}
        ),
        "interpretation": None,
        "component": None,
        "comment": None,
        "performer": None,
        "biomarker_id": str(b_def.id),
        "biomarker_slug": getattr(b_def, "slug", None),
        "biomarker_info": getattr(b_def, "info", None),
        "biomarker_aliases": getattr(b_def, "aliases", None) or [],
        "biomarker_value_type": vt,
        "biomarker_supports_multi_state": getattr(b_def, "supports_multi_state", False),
        "biomarker_reference_range_min": b_def.reference_range_min,
        "biomarker_reference_range_max": b_def.reference_range_max,
        "raw_value": value,
        "normalized_value": value,
        "lab_reference_range": None,
        "normalized_unit": unit,
        "relative_score": _relative_score(
            value, b_def.reference_range_min, b_def.reference_range_max
        ),
        "method": None,
        "examination_id": None,
        "document_id": None,
        "patient_id": str(row.get("patient_id")) if row.get("patient_id") else None,
    }


def _effective_datetime_key(row: dict[str, Any]) -> datetime.datetime:
    """Sort key for the merged read: ISO ``effective_datetime`` -> UTC datetime."""
    ts = row.get("effective_datetime")
    if not ts:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _dedup_by_biomarker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop cross-source duplicates, keeping the newer row per biomarker.

    The FHIR side dedups by ``biomarker_id`` and the telemetry side by slug, but
    a biomarker with residue in both stores (a failed/in-progress migration)
    would otherwise appear twice. Keyed by ``biomarker_id`` when present, else
    by ``biomarker_slug``, else by row ``id`` (always unique). The newest row
    (by ``effective_datetime``) wins regardless of source, so stale residue is
    dropped whether the biomarker is currently FHIR or telemetry.
    """
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("biomarker_id") or row.get("biomarker_slug") or row.get("id")
        existing = deduped.get(key)
        if existing is None or _effective_datetime_key(row) > _effective_datetime_key(
            existing
        ):
            deduped[key] = row
    return list(deduped.values())


# --- Payloads for Two-Way Contract ---


class ClientRecord(BaseModel):
    type: str = Field(..., description="'quantitative' or 'categorical'")
    biomarker_id: str | None = Field(
        None, description="UUID of the mapped biomarker definition"
    )
    code: str | None = None
    coding_system: str = Field(default="custom")
    name: str
    value: float | None = None
    value_string: str | None = None
    unit: str | None = None
    timestamp: str | None = None
    reference_range: dict[str, float] | None = None
    interpretation: str | None = None
    performer: str | None = None


class ClientExaminationRecord(BaseModel):
    id: str | None = None  # External ID (e.g., myhealth reportId)
    date: str | None = None  # Result Date
    lab_name: str | None = None  # Map to organization internally
    notes: str | None = None  # Clinician notes
    patient_notes: str | None = None
    category: str | None = None  # e.g., "Blood Test", "LIS Report"
    diagnoses: list[str] | None = Field(default_factory=list)
    impressions: str | None = None
    records: list[ClientRecord] | None = None  # The nested biomarkers


class SyncPayload(BaseModel):
    client_version: str
    source_system: str
    cursor: str | None = None
    records: list[ClientRecord] | None = None
    examinations: list[ClientExaminationRecord] | None = None


class MapRequestPayload(BaseModel):
    unmapped_metrics: list[MetricMappingRequest]


class HealthAssistantBridgeProvider(BaseHealthProvider):
    domain = "health_assistant_bridge"

    async def handle_api_request(
        self, integration: UserIntegration, path: str, method: str, request: Any
    ) -> dict[str, Any]:
        """Handle two-way API requests from headless clients.

        Auth is enforced entirely by the platform endpoint
        (``integration_api_proxy``): when an ``api_secret`` is configured the
        route verifies ``X-Api-Signature`` (HMAC-SHA256,
        ``METHOD\\n<path>\\n[<timestamp>\\n]<raw_body>``) on **every** path
        before dispatch reaches this handler. The provider therefore never
        re-verifies — the endpoint is the single auth chokepoint, and a
        request arriving here is already authenticated (either by a valid
        signature when a secret is set, or by UUID-knowledge in legacy mode).
        """

        # Log the request details for debugging
        await self.log_debug_payload(
            integration,
            f"API Request: {method} /{path}",
            {"path": path, "method": method},
        )

        if path == "status" and method == "GET":
            # Load the manifest to get the latest SDK versions
            import asyncio
            import json
            import os

            manifest_path = os.path.join(os.path.dirname(__file__), "manifest.json")

            def _read_manifest() -> dict:
                with open(manifest_path, "r") as f:
                    return json.load(f)

            sdks = {}
            if os.path.exists(manifest_path):
                try:
                    sdks = (await asyncio.to_thread(_read_manifest)).get("sdks", {})
                except (OSError, ValueError) as e:
                    logger.error("Failed to read manifest for sdks: %s", e)

            # The frontend/PWA origin for the mobile app's "Open in browser"
            # deep links. Returned here (over the ktor connection the app
            # already uses) so the app doesn't need a second network stack to
            # discover it. Same resolution as GET /api/v1/config/public.
            from app.core.database import AsyncSessionLocal
            from app.core.public_config import resolve_public_config

            try:
                async with AsyncSessionLocal() as db:
                    public_urls = await resolve_public_config(db)
            except Exception:
                public_urls = await resolve_public_config(None)

            return {
                "status": "active",
                "integration_id": str(integration.id),
                "last_synced_at": integration.last_synced_at.isoformat()
                if integration.last_synced_at
                else None,
                "cursor": self.get_sync_cursor(integration, "last_timestamp"),
                "latest_sdks": sdks,
                "frontend_base_url": public_urls["frontend_base_url"],
            }

        elif path == "map" and method == "POST":
            # The client asks the backend to map raw names to existing catalog entries via LLM
            try:
                payload_data = await request.json()
                map_request = MapRequestPayload(**payload_data)
            except (TypeError, ValueError, RuntimeError) as e:
                raise ValueError(f"Invalid payload format: {e}")

            return await self._handle_map_request(integration, map_request)

        elif path == "sync" and method == "POST":
            # The client pushes data here
            try:
                payload_data = await request.json()
                sync_payload = SyncPayload(**payload_data)
            except (TypeError, ValueError, RuntimeError) as e:
                raise ValueError(f"Invalid Sync payload format: {e}")

            await self.log_debug_payload(
                integration,
                f"Sync Payload ({sync_payload.source_system})",
                payload_data,
            )

            # Use universal parsing logic
            builder = self.create_observation_builder(integration)

            try:
                inserted_count = await self._process_and_save_sync_data(
                    integration, sync_payload, builder
                )

                # Update the cursor if provided by the client
                if sync_payload.cursor:
                    self.set_sync_cursor(
                        integration, "last_timestamp", sync_payload.cursor
                    )

                return {
                    "success": True,
                    "metrics_synced": inserted_count,
                    "message": "Data synchronized successfully",
                }
            except Exception as e:
                logger.exception("[%s] Sync failed", self.domain)
                return {"success": False, "error": str(e)}

        # --- Phase 4: read/management paths (patient-scoped to the bound instance) ---
        elif path == "observations/latest" and method == "GET":
            return await self._read_observations_latest(integration, request)
        elif path == "observations" and method == "GET":
            return await self._read_observations(integration, request)
        elif path == "biomarkers" and method == "GET":
            return await self._read_biomarkers(integration, request)
        elif path == "examinations" and method == "GET":
            return await self._read_examinations(integration, request)
        elif path == "examinations" and method == "POST":
            return await self._create_examination(integration, request)
        elif path.startswith("examinations/") and method == "POST":
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "documents":
                return await self._upload_document(integration, parts[1], request)
            raise NotImplementedError(
                f"POST /{path} is not supported by the bridge API."
            )
        elif path.startswith("examinations/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_examination(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path.startswith("examinations/") and method == "GET":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._read_examination_detail(integration, parts[1])
            if len(parts) == 3 and parts[2] == "documents":
                return await self._list_documents(integration, parts[1], request)
            if len(parts) == 3 and parts[2] == "status":
                return await self._examination_extraction_status(integration, parts[1])
            if len(parts) == 3 and parts[2] == "logs":
                return await self._extraction_logs(integration, parts[1])
            raise NotImplementedError(
                f"GET /{path} is not supported by the bridge API."
            )
        elif path == "documents" and method == "GET":
            return await self._list_documents_all(integration, request)
        elif path.startswith("documents/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_document(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path.startswith("documents/") and method == "POST":
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "extract":
                return await self._trigger_document_extraction(integration, parts[1])
            raise NotImplementedError(
                f"POST /{path} is not supported by the bridge API."
            )
        elif path.startswith("documents/") and method == "GET":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._read_document_detail(integration, parts[1])
            if len(parts) == 3 and parts[2] == "content":
                return await self._send_document_content(integration, parts[1])
            if len(parts) == 3 and parts[2] == "preview":
                return await self._send_document_preview(integration, parts[1], request)
            if len(parts) == 4 and parts[2] == "extract" and parts[3] == "status":
                return await self._document_extraction_status(integration, parts[1])
            raise NotImplementedError(
                f"GET /{path} is not supported by the bridge API."
            )
        # --- Phase 3: clinical-record read paths (patient-scoped) ---
        elif path == "medications" and method == "GET":
            return await self._read_medications(integration, request)
        elif path == "allergies" and method == "GET":
            return await self._read_allergies(integration, request)
        elif path == "vaccines" and method == "GET":
            return await self._read_vaccines(integration, request)
        elif path == "clinical-events" and method == "GET":
            return await self._read_clinical_events(integration, request)
        elif path.startswith("clinical-events/") and method == "GET":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._read_clinical_event_detail(integration, parts[1])
            raise NotImplementedError(
                f"GET /{path} is not supported by the bridge API."
            )
        elif path == "doctors" and method == "GET":
            return await self._read_doctors(integration, request)
        elif path == "changes" and method == "GET":
            return await self._changes_since(integration, request)
        # --- Phase 6: clinical-record mutations (patient-scoped to the bound instance) ---
        elif path == "medications" and method == "POST":
            return await self._create_medication(integration, request)
        elif path.startswith("medications/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._update_medication(integration, parts[1], request)
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path.startswith("medications/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_medication(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path == "allergies" and method == "POST":
            return await self._create_allergy(integration, request)
        elif path.startswith("allergies/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._update_allergy(integration, parts[1], request)
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path.startswith("allergies/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_allergy(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path == "vaccines" and method == "POST":
            return await self._create_vaccine(integration, request)
        elif path.startswith("vaccines/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._update_vaccine(integration, parts[1], request)
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path.startswith("vaccines/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_vaccine(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path == "clinical-events" and method == "POST":
            return await self._create_clinical_event(integration, request)
        elif path.startswith("clinical-events/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._update_clinical_event(integration, parts[1], request)
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path.startswith("clinical-events/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_clinical_event(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path.startswith("clinical-events/") and method == "POST":
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "occurrences":
                return await self._add_clinical_event_occurrence(
                    integration, parts[1], request
                )
            raise NotImplementedError(
                f"POST /{path} is not supported by the bridge API."
            )
        elif path == "doctors" and method == "POST":
            return await self._create_doctor(integration, request)
        elif path.startswith("doctors/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._update_doctor(integration, parts[1], request)
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path.startswith("doctors/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._delete_doctor(integration, parts[1])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        # --- Phase 7: notification inbox + preferences (owner-scoped, NOT patient-scoped) ---
        # The bridge integration is bound to one patient, but notifications are
        # addressed to the *owner* (the user who onboarded the integration).
        # The inbox + preferences therefore key on ``integration.user_id`` +
        # ``integration.tenant_id``, never on ``_bound_patient_id``.
        elif path == "notifications/inbox" and method == "GET":
            return await self._notification_inbox(integration, request)
        elif path == "notifications/unread-count" and method == "GET":
            return await self._notification_unread_count(integration)
        elif path == "notifications/read-all" and method == "POST":
            return await self._notification_read_all(integration)
        elif path == "notifications/preferences" and method == "GET":
            return await self._notification_preferences(integration)
        elif path.startswith("notifications/preferences/") and method == "PUT":
            parts = path.split("/")
            if len(parts) == 3:
                return await self._notification_set_preference(
                    integration, parts[2], request
                )
            raise NotImplementedError(
                f"PUT /{path} is not supported by the bridge API."
            )
        elif path == "notifications/triggers" and method == "GET":
            return await self._notification_list_triggers(integration, request)
        elif path == "notifications/triggers" and method == "POST":
            return await self._notification_create_trigger(integration, request)
        elif path.startswith("notifications/triggers/") and method == "DELETE":
            parts = path.split("/")
            if len(parts) == 3:
                return await self._notification_delete_trigger(
                    integration, parts[2]
                )
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path.startswith("notifications/") and method == "PATCH":
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "read":
                return await self._notification_mark(
                    integration, parts[1], "read"
                )
            if len(parts) == 3 and parts[2] == "dismiss":
                return await self._notification_mark(
                    integration, parts[1], "dismiss"
                )
            raise NotImplementedError(
                f"PATCH /{path} is not supported by the bridge API."
            )
        # --- Phase 8: native push device registration (owner-scoped) ---
        elif path == "notifications/register-device" and method == "POST":
            return await self._register_push_device(integration, request)
        elif (
            path.startswith("notifications/register-device/") and method == "DELETE"
        ):
            parts = path.split("/")
            if len(parts) == 3:
                return await self._unregister_push_device(integration, parts[2])
            raise NotImplementedError(
                f"DELETE /{path} is not supported by the bridge API."
            )
        elif path == "devices" and method == "GET":
            return await self._list_push_devices(integration)
        else:
            raise NotImplementedError(
                f"Path '{path}' with method '{method}' is not supported by the bridge API."
            )

    def _bound_patient_id(self, integration: UserIntegration):
        """The one patient this instance can act on. Every read/create MUST
        filter by this — the resolved actor carries the OWNER's role (which can
        be ADMIN), so patient isolation is this explicit filter, not automatic.
        Mobile-app plan §4 "Patient scoping"."""
        from uuid import UUID

        pid = getattr(integration, "patient_id", None)
        if pid is None:
            raise ValueError("Bridge instance is not bound to a patient.")
        return UUID(str(pid))

    @staticmethod
    def _read_envelope(data: Any, cursor: str | None = None) -> dict[str, Any]:
        return {
            "data": data,
            "cursor": cursor,
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    async def _read_observations_latest(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """Latest value **per biomarker** (merged FHIR + telemetry).

        Merges ``list_observations_latest`` (one row per FHIR biomarker) with
        ``get_patient_telemetry_latest`` (one row per telemetry slug),
        normalizes both to the same ObservationPoint shape, sorts by effective
        time desc, and caps the merged list at the requested ``limit``.
        """
        from sqlalchemy import or_, select

        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition
        from app.services.fhir_service import list_observations_latest
        from app.services.telemetry_service import get_patient_telemetry_latest

        patient_id = self._bound_patient_id(integration)
        try:
            limit = int(request.query_params.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        items: list[dict[str, Any]] = []
        async with AsyncSessionLocal() as db:
            fhir_result = await list_observations_latest(
                tenant_id=integration.tenant_id, patient_id=patient_id, limit=limit
            )
            for row in fhir_result.get("items", []):
                row["reference_range"] = _flatten_reference_range(
                    row.get("reference_range")
                )
                items.append(row)

            try:
                tele_rows = await get_patient_telemetry_latest(
                    db,
                    tenant_id=integration.tenant_id,
                    patient_id=patient_id,
                    limit=limit,
                )
            except Exception:
                logger.exception(
                    "Telemetry latest read failed for integration %s; "
                    "returning FHIR-only",
                    integration.id,
                )
                tele_rows = []
            if tele_rows:
                slugs = [r["slug"] for r in tele_rows if r.get("slug")]
                defs: dict[str, Any] = {}
                if slugs:
                    res = await db.execute(
                        select(BiomarkerDefinition).where(
                            or_(
                                BiomarkerDefinition.tenant_id == integration.tenant_id,
                                BiomarkerDefinition.tenant_id.is_(None),
                            ),
                            BiomarkerDefinition.slug.in_(slugs),
                        )
                    )
                    defs = {b.slug: b for b in res.scalars().all()}
                for row in tele_rows:
                    b_def = defs.get(row.get("slug"))
                    if b_def is None:
                        continue
                    items.append(
                        _observation_point_from_telemetry(row, b_def, patient_id)
                    )

        # Dedup across sources: a biomarker with residue in both stores (a
        # failed/in-progress migration) must appear once — the newer row wins.
        items = _dedup_by_biomarker(items)
        items.sort(key=_effective_datetime_key, reverse=True)
        return self._read_envelope(items[:limit])

    async def _read_observations(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.fhir_service import (
            list_observations,
            resolve_biomarker_definition,
        )
        from app.services.telemetry_service import get_patient_telemetry_series

        patient_id = self._bound_patient_id(integration)
        qp = request.query_params
        try:
            limit = min(int(qp.get("limit", "200")), 500)
        except (TypeError, ValueError):
            limit = 200
        biomarker = qp.get("biomarker")
        async with AsyncSessionLocal() as db:
            if biomarker:
                b_def = await resolve_biomarker_definition(
                    db, integration.tenant_id, biomarker
                )
                if b_def is not None and b_def.is_telemetry:
                    try:
                        rows = await get_patient_telemetry_series(
                            db,
                            tenant_id=integration.tenant_id,
                            patient_id=patient_id,
                            slug=b_def.slug,
                            start_date=qp.get("since"),
                            end_date=qp.get("until"),
                            limit=limit,
                        )
                    except Exception:
                        logger.exception(
                            "Telemetry series read failed for integration %s "
                            "(slug=%s); falling back to FHIR",
                            integration.id,
                            b_def.slug,
                        )
                        rows = []
                    if rows:
                        items = [
                            _observation_point_from_telemetry(row, b_def, patient_id)
                            for row in rows
                        ]
                        return self._read_envelope(items)
            result = await list_observations(
                tenant_id=integration.tenant_id,
                patient_id=patient_id,
                code=biomarker,
                start_date=qp.get("since"),
                end_date=qp.get("until"),
                limit=limit,
            )
        items = result.get("items", [])
        for row in items:
            row["reference_range"] = _flatten_reference_range(
                row.get("reference_range")
            )
        return self._read_envelope(items)

    async def _read_biomarkers(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from sqlalchemy import or_, select

        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition

        try:
            limit = min(int(request.query_params.get("limit", "500")), 1000)
        except (TypeError, ValueError):
            limit = 500
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(BiomarkerDefinition)
                .where(
                    or_(
                        BiomarkerDefinition.tenant_id == integration.tenant_id,
                        BiomarkerDefinition.tenant_id.is_(None),
                    )
                )
                .order_by(BiomarkerDefinition.name.asc())
                .limit(limit)
            )
            items = []
            for b in res.scalars().all():
                coding_system = getattr(b, "coding_system", None)
                if hasattr(coding_system, "value"):
                    coding_system = coding_system.value
                vt = getattr(b, "value_type", None)
                if hasattr(vt, "value"):
                    vt = vt.value
                items.append(
                    {
                        "id": str(b.id),
                        "name": b.name,
                        "slug": getattr(b, "slug", None),
                        "code": b.code,
                        "coding_system": coding_system,
                        # ``preferred_unit`` is the relationship — the old
                        # ``default_unit`` getattr always returned None.
                        "unit": b.preferred_unit.symbol if b.preferred_unit else None,
                        "is_telemetry": bool(getattr(b, "is_telemetry", False)),
                        "reference_range_min": b.reference_range_min,
                        "reference_range_max": b.reference_range_max,
                        "value_type": vt,
                    }
                )
        return self._read_envelope(items)

    async def _read_examinations(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.examination_model import ExaminationModel

        patient_id = self._bound_patient_id(integration)
        try:
            limit = min(int(request.query_params.get("limit", "50")), 200)
        except (TypeError, ValueError):
            limit = 50
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ExaminationModel)
                .where(
                    ExaminationModel.tenant_id == integration.tenant_id,
                    ExaminationModel.patient_id == patient_id,
                )
                .order_by(ExaminationModel.examination_date.desc())
                .limit(limit)
            )
            items = [
                {
                    "id": str(e.id),
                    "examination_date": e.examination_date.isoformat()
                    if e.examination_date
                    else None,
                    "notes": e.notes,
                    "patient_notes": e.patient_notes,
                    "extraction_status": e.extraction_status,
                }
                for e in res.scalars().all()
            ]
        return self._read_envelope(items)

    async def _read_examination_detail(
        self, integration: UserIntegration, exam_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            e = await self._bound_examination(db, integration, exam_id)
            return {
                "id": str(e.id),
                "examination_date": e.examination_date.isoformat()
                if e.examination_date
                else None,
                "notes": e.notes,
                "patient_notes": e.patient_notes,
                "extraction_status": e.extraction_status,
                "diagnoses": e.diagnoses,
                "impressions": e.impressions,
            }

    async def _bound_examination(self, db, integration: UserIntegration, exam_id: str):
        """Load an examination scoped to the bound patient; raises ValueError if
        the id is malformed or belongs to a different patient. The linchpin of
        patient isolation for the `/examinations/{id}/*` paths (plan §4)."""
        from uuid import UUID

        from sqlalchemy import select

        from app.models.examination_model import ExaminationModel

        patient_id = self._bound_patient_id(integration)
        try:
            eid = UUID(exam_id)
        except ValueError:
            raise ValueError(f"Invalid examination id: {exam_id}")
        res = await db.execute(
            select(ExaminationModel).where(
                ExaminationModel.id == eid,
                ExaminationModel.tenant_id == integration.tenant_id,
                ExaminationModel.patient_id == patient_id,
            )
        )
        e = res.scalars().one_or_none()
        if e is None:
            raise ValueError("Examination not found for this patient.")
        return e

    async def _list_documents(
        self, integration: UserIntegration, exam_id: str, request: Any
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel

        patient_id = self._bound_patient_id(integration)
        async with AsyncSessionLocal() as db:
            exam = await self._bound_examination(db, integration, exam_id)
            res = await db.execute(
                select(DocumentModel)
                .where(
                    DocumentModel.examination_id == exam.id,
                    DocumentModel.tenant_id == integration.tenant_id,
                    DocumentModel.patient_id == patient_id,
                    DocumentModel.deleted_at.is_(None),
                )
                .order_by(DocumentModel.created_at.desc())
            )
            items = [self._document_summary(d) for d in res.scalars().all()]
        return self._read_envelope(items)

    @staticmethod
    def _document_summary(d: Any) -> dict[str, Any]:
        """Build the metadata dict for a document, enriched with
        ``content_type`` (MIME-guessed from the filename), ``file_size``
        (bytes on disk, ``None`` if the file is missing), and
        ``examination_id``. Used by every document list/detail path so
        the wire shape is identical across ``GET /documents``,
        ``GET /documents/{id}``, and ``GET /examinations/{id}/documents``.
        """
        import mimetypes
        import os

        content_type = None
        file_size = None
        file_path = getattr(d, "file_path", None)
        if file_path and os.path.exists(file_path):
            content_type, _ = mimetypes.guess_type(d.filename or file_path)
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                file_size = None
        return {
            "id": str(d.id),
            "filename": d.filename,
            "status": d.status,
            "progress": d.progress,
            "external_id": d.external_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "content_type": content_type,
            "file_size": file_size,
            "examination_id": str(d.examination_id) if d.examination_id else None,
        }

    async def _bound_document(self, db: Any, integration: UserIntegration, doc_id: str):
        """Load a document scoped to the bound patient; raises ``ValueError``
        if the id is malformed or belongs to a different patient (or another
        tenant, or has been soft-deleted). The linchpin of patient isolation
        for the ``/documents/{id}/*`` paths — mirrors ``_bound_examination``.
        """
        from uuid import UUID

        from sqlalchemy import select

        from app.models.document_model import DocumentModel

        patient_id = self._bound_patient_id(integration)
        try:
            did = UUID(doc_id)
        except ValueError:
            raise ValueError(f"Invalid document id: {doc_id}")
        res = await db.execute(
            select(DocumentModel).where(
                DocumentModel.id == did,
                DocumentModel.tenant_id == integration.tenant_id,
                DocumentModel.patient_id == patient_id,
                DocumentModel.deleted_at.is_(None),
            )
        )
        d = res.scalars().one_or_none()
        if d is None:
            raise ValueError("Document not found for this patient.")
        return d

    async def _list_documents_all(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """Patient-wide document list. Optional ``?examination_id=`` filter
        narrows to one exam; ``?limit=`` caps the page (max 500, default 100).
        Newest first."""
        from uuid import UUID

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel

        patient_id = self._bound_patient_id(integration)
        qp = request.query_params
        try:
            limit = min(int(qp.get("limit", "100")), 500)
        except (TypeError, ValueError):
            limit = 100
        stmt = select(DocumentModel).where(
            DocumentModel.tenant_id == integration.tenant_id,
            DocumentModel.patient_id == patient_id,
            DocumentModel.deleted_at.is_(None),
        )
        exam_filter = qp.get("examination_id")
        if exam_filter:
            try:
                stmt = stmt.where(DocumentModel.examination_id == UUID(exam_filter))
            except ValueError:
                raise ValueError(f"Invalid examination_id: {exam_filter}")
        stmt = stmt.order_by(DocumentModel.created_at.desc()).limit(limit)
        async with AsyncSessionLocal() as db:
            res = await db.execute(stmt)
            items = [self._document_summary(d) for d in res.scalars().all()]
        return self._read_envelope(items)

    async def _read_document_detail(
        self, integration: UserIntegration, doc_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            return self._document_summary(d)

    async def _send_document_content(
        self, integration: UserIntegration, doc_id: str
    ) -> Any:
        """Binary content of a document's stored file. Returns a
        ``fastapi.responses.Response`` with the bytes pre-loaded (rather than a
        streaming ``FileResponse``) so any caller — the platform proxy, direct
        tests, future non-ASGI contexts — can read ``response.body`` directly.
        The 25 MiB upload cap bounds the memory cost. The bridge's single HMAC
        credential authenticates the call; the patient filter on
        ``_bound_document`` enforces isolation."""
        import mimetypes
        import os

        from fastapi.responses import Response

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            file_path = getattr(d, "file_path", None)
            filename = d.filename
        if not file_path or not os.path.exists(file_path):
            raise ValueError("Document file not found on disk.")
        content_type, _ = mimetypes.guess_type(filename or file_path)

        def _read() -> bytes:
            with open(file_path, "rb") as f:
                return f.read()

        import asyncio

        content = await asyncio.to_thread(_read)
        return Response(
            content=content,
            media_type=content_type or "application/octet-stream",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    async def _send_document_preview(
        self, integration: UserIntegration, doc_id: str, request: Any
    ) -> Any:
        """JPEG preview of a document (proxies the existing
        ``endpoints/documents.py`` conversion logic). For images, the stored
        bytes are returned with their guessed MIME. For PDF/DICOM,
        ``convert_to_images`` renders JPEG page renders; ``?page=N`` selects
        a page (default 0; clamped to ``[0, len(images))``).
        """
        import mimetypes
        from pathlib import Path

        from fastapi.responses import Response

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            file_path = Path(getattr(d, "file_path", ""))
            filename = d.filename
        if not file_path.exists():
            raise ValueError("Document file not found on disk.")
        try:
            page = int(request.query_params.get("page", "0"))
        except (TypeError, ValueError):
            page = 0

        # Images: serve as-is.
        if (
            (filename or "")
            .lower()
            .endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
        ):
            content_type, _ = mimetypes.guess_type(filename)

            def _read() -> bytes:
                with open(file_path, "rb") as f:
                    return f.read()

            import asyncio

            return Response(
                content=await asyncio.to_thread(_read),
                media_type=content_type or "image/jpeg",
            )

        # PDF / DICOM: render to JPEG via the OCR conversion helper.
        from app.ai.processors.ocr.utils import convert_to_images

        try:
            images = await convert_to_images(file_path)
        except Exception:
            logger.exception(
                "Bridge document preview conversion failed (doc_id=%s)", doc_id
            )
            raise ValueError("Failed to generate preview image.")
        if not images:
            raise ValueError("Failed to generate preview image.")
        if page < 0 or page >= len(images):
            page = 0
        return Response(
            content=images[page],
            media_type="image/jpeg",
            headers={
                "X-Total-Pages": str(len(images)),
                "X-Current-Page": str(page),
            },
        )

    # --- Phase 3: clinical-record read paths (patient-scoped) -------------

    async def _read_medications(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /medications` — the bound patient's medication instances.
        Newest by ``start_date`` desc (NULLs last). The patient's full history
        including inactive meds; the client filters if it wants only active."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.fhir.medication import Medication

        patient_id = self._bound_patient_id(integration)
        try:
            limit = min(int(request.query_params.get("limit", "200")), 500)
        except (TypeError, ValueError):
            limit = 200
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Medication)
                .where(
                    Medication.tenant_id == integration.tenant_id,
                    Medication.patient_id == patient_id,
                    Medication.deleted_at.is_(None),
                )
                .order_by(Medication.start_date.desc().nullslast())
                .limit(limit)
            )
            items = [
                {
                    "id": str(m.id),
                    "status": m.status.value
                    if hasattr(m.status, "value")
                    else m.status,
                    "intent": m.intent.value
                    if hasattr(m.intent, "value")
                    else m.intent,
                    "code": m.code,
                    "start_date": m.start_date.isoformat() if m.start_date else None,
                    "end_date": m.end_date.isoformat() if m.end_date else None,
                    "dosage": m.dosage,
                    "frequency": m.frequency,
                    "reason": m.reason,
                    "note": m.note,
                    "examination_id": str(m.examination_id)
                    if m.examination_id
                    else None,
                    "source_integration_id": str(m.source_integration_id)
                    if m.source_integration_id
                    else None,
                    "external_id": m.external_id,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                }
                for m in res.scalars().all()
            ]
        return self._read_envelope(items)

    async def _read_allergies(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /allergies` — the bound patient's allergy-intolerance instances.
        ``?active=true`` (the default) filters to active+verified; pass
        ``?active=false`` for the full history (including resolved)."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.fhir.allergy import AllergyIntolerance

        patient_id = self._bound_patient_id(integration)
        try:
            limit = min(int(request.query_params.get("limit", "200")), 500)
        except (TypeError, ValueError):
            limit = 200
        active_only = request.query_params.get("active", "true").lower() != "false"
        stmt = select(AllergyIntolerance).where(
            AllergyIntolerance.tenant_id == integration.tenant_id,
            AllergyIntolerance.patient_id == patient_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
        if active_only:
            stmt = stmt.where(AllergyIntolerance.clinical_status == "ACTIVE")
        stmt = stmt.order_by(AllergyIntolerance.onset_date.desc().nullslast()).limit(
            limit
        )
        async with AsyncSessionLocal() as db:
            res = await db.execute(stmt)
            items = [
                {
                    "id": str(a.id),
                    "clinical_status": a.clinical_status.value
                    if hasattr(a.clinical_status, "value")
                    else a.clinical_status,
                    "verification_status": a.verification_status,
                    "category": a.category.value
                    if hasattr(a.category, "value")
                    else a.category,
                    "criticality": a.criticality.value
                    if hasattr(a.criticality, "value")
                    else a.criticality,
                    "code": a.code,
                    "onset_date": a.onset_date.isoformat() if a.onset_date else None,
                    "resolved_date": a.resolved_date.isoformat()
                    if a.resolved_date
                    else None,
                    "last_occurrence": a.last_occurrence.isoformat()
                    if a.last_occurrence
                    else None,
                    "note": a.note,
                    "reactions": a.reactions,
                    "source_integration_id": str(a.source_integration_id)
                    if a.source_integration_id
                    else None,
                    "external_id": a.external_id,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                }
                for a in res.scalars().all()
            ]
        return self._read_envelope(items)

    async def _read_vaccines(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /vaccines` — the bound patient's immunizations, newest first."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.fhir.vaccine import PatientImmunization

        patient_id = self._bound_patient_id(integration)
        try:
            limit = min(int(request.query_params.get("limit", "200")), 500)
        except (TypeError, ValueError):
            limit = 200
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(PatientImmunization)
                .where(
                    PatientImmunization.tenant_id == integration.tenant_id,
                    PatientImmunization.patient_id == patient_id,
                    PatientImmunization.deleted_at.is_(None),
                )
                .order_by(PatientImmunization.administered_at.desc().nullslast())
                .limit(limit)
            )
            items = [
                {
                    "id": str(v.id),
                    "status": v.status.value
                    if hasattr(v.status, "value")
                    else v.status,
                    "vaccine_code": v.vaccine_code,
                    "administered_at": v.administered_at.isoformat()
                    if v.administered_at
                    else None,
                    "dose_number": v.dose_number,
                    "lot_number": v.lot_number,
                    "manufacturer": v.manufacturer,
                    "location": v.location,
                    "note": v.note,
                    "examination_id": str(v.examination_id)
                    if v.examination_id
                    else None,
                    "source_integration_id": str(v.source_integration_id)
                    if v.source_integration_id
                    else None,
                    "external_id": v.external_id,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                    "updated_at": v.updated_at.isoformat() if v.updated_at else None,
                }
                for v in res.scalars().all()
            ]
        return self._read_envelope(items)

    async def _read_clinical_events(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /clinical-events` — flat list (no nested relations, fast).
        ``?status=active`` filters to currently-active events. Use
        `GET /clinical-events/{id}` for the full nested detail."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.clinical_event import ClinicalEvent

        patient_id = self._bound_patient_id(integration)
        try:
            limit = min(int(request.query_params.get("limit", "100")), 500)
        except (TypeError, ValueError):
            limit = 100
        status = request.query_params.get("status")
        stmt = select(ClinicalEvent).where(
            ClinicalEvent.tenant_id == integration.tenant_id,
            ClinicalEvent.patient_id == patient_id,
            ClinicalEvent.deleted_at.is_(None),
        )
        if status:
            stmt = stmt.where(ClinicalEvent.status == status)
        stmt = stmt.order_by(ClinicalEvent.onset_date.desc().nullslast()).limit(limit)
        async with AsyncSessionLocal() as db:
            res = await db.execute(stmt)
            items = [self._clinical_event_summary(e) for e in res.scalars().all()]
        return self._read_envelope(items)

    async def _read_clinical_event_detail(
        self, integration: UserIntegration, event_id: str
    ) -> dict[str, Any]:
        """`GET /clinical-events/{id}` — full nested detail via the model's
        ``to_dict()`` (type_details, examinations, observations, anatomy_links).
        Patient-scoped via ``_bound_clinical_event``. Eager-loads the same
        relationship chain ``clinical_event_service._event_eager_loads`` loads
        so ``to_dict()`` doesn't hit lazy-load N+1s (which would fail under
        async — MissingGreenlet)."""
        from uuid import UUID

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.clinical_event import (
            ClinicalEvent,
            ClinicalEventType,
            EventExaminationLink,
            EventObservationLink,
        )
        from app.models.fhir.patient import Observation

        patient_id = self._bound_patient_id(integration)
        try:
            eid = UUID(event_id)
        except ValueError:
            raise ValueError(f"Invalid clinical event id: {event_id}")
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ClinicalEvent)
                .options(
                    selectinload(ClinicalEvent.type_entity).selectinload(
                        ClinicalEventType.category_concept
                    ),
                    selectinload(ClinicalEvent.examination_links).selectinload(
                        EventExaminationLink.examination
                    ),
                    selectinload(ClinicalEvent.observation_links)
                    .selectinload(EventObservationLink.observation)
                    .selectinload(Observation.biomarker)
                    .selectinload(BiomarkerDefinition.preferred_unit),
                    selectinload(ClinicalEvent.occurrence_links),
                    selectinload(ClinicalEvent.anatomy_links),
                )
                .where(
                    ClinicalEvent.id == eid,
                    ClinicalEvent.tenant_id == integration.tenant_id,
                    ClinicalEvent.patient_id == patient_id,
                    ClinicalEvent.deleted_at.is_(None),
                )
            )
            e = res.scalars().one_or_none()
            if e is None:
                raise ValueError("Clinical event not found for this patient.")
            return e.to_dict()

    @staticmethod
    def _clinical_event_summary(e: Any) -> dict[str, Any]:
        """Flat list item for clinical events — no nested relations. The detail
        endpoint returns the full ``to_dict()``; this keeps the list fast (no
        N+1 over type/anatomy/observations)."""
        status = e.status.value if hasattr(e.status, "value") else e.status
        coding_system = (
            e.coding_system.value
            if hasattr(e.coding_system, "value")
            else e.coding_system
        )
        type_entity = getattr(e, "type_entity", None)
        return {
            "id": str(e.id),
            "patient_id": str(e.patient_id) if e.patient_id else None,
            "tenant_id": str(e.tenant_id) if e.tenant_id else None,
            "type_id": str(e.type_id) if e.type_id else None,
            "type_name": getattr(type_entity, "name", None),
            "type_slug": getattr(type_entity, "slug", None),
            "type_icon": getattr(type_entity, "icon", None),
            "type_color": getattr(type_entity, "color", None),
            "status": status,
            "title": e.title,
            "description": e.description,
            "onset_date": e.onset_date.isoformat() if e.onset_date else None,
            "resolved_date": e.resolved_date.isoformat() if e.resolved_date else None,
            "coding_system": coding_system,
            "code": e.code,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        }

    async def _bound_clinical_event(
        self, db: Any, integration: UserIntegration, event_id: str
    ):
        """Load a clinical event scoped to the bound patient (mirrors
        ``_bound_examination`` / ``_bound_document``)."""
        from uuid import UUID

        from sqlalchemy import select

        from app.models.clinical_event import ClinicalEvent

        patient_id = self._bound_patient_id(integration)
        try:
            eid = UUID(event_id)
        except ValueError:
            raise ValueError(f"Invalid clinical event id: {event_id}")
        res = await db.execute(
            select(ClinicalEvent).where(
                ClinicalEvent.id == eid,
                ClinicalEvent.tenant_id == integration.tenant_id,
                ClinicalEvent.patient_id == patient_id,
                ClinicalEvent.deleted_at.is_(None),
            )
        )
        e = res.scalars().one_or_none()
        if e is None:
            raise ValueError("Clinical event not found for this patient.")
        return e

    async def _read_doctors(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /doctors` — tenant-wide doctor address book (the bound patient's
        owner's tenant). Doctors aren't patient-scoped (they're shared within a
        tenant); the bridge returns what the owner's role permits. Specialty
        concept is selectin-loaded on the model."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.doctor_model import DoctorModel

        try:
            limit = min(int(request.query_params.get("limit", "200")), 500)
        except (TypeError, ValueError):
            limit = 200
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(DoctorModel)
                .where(DoctorModel.tenant_id == integration.tenant_id)
                .order_by(DoctorModel.name.asc())
                .limit(limit)
            )
            items = [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "specialty_concept_id": str(d.specialty_concept_id)
                    if d.specialty_concept_id
                    else None,
                    "specialty": d.specialty,
                    "license_number": d.license_number,
                    "email": d.email,
                    "phone": d.phone,
                    "telecom": d.telecom,
                    "address": d.address,
                    "office_number": d.office_number,
                    "office_details": d.office_details,
                }
                for d in res.scalars().all()
            ]
        return self._read_envelope(items)

    # --- Phase 4: mutations + extraction status --------------------------

    # --- Phase 5: unified /changes delta + mutations --------------------

    async def _changes_since(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /changes?since=<ISO>&types=<csv>&limit=` — unified delta for
        two-way incremental sync. One round-trip replaces N per-type reads;
        powers the app's pull-to-refresh + the 15-min wake-up poll.

        Each requested type is queried by ``updated_at > since`` AND the bound
        patient + tenant + ``deleted_at IS NULL``. The response carries the
        per-type arrays inside ``data`` plus a ``cursor`` (the
        ``max(updated_at)`` across the batch) for the next poll. If nothing
        changed, ``cursor`` is ``null`` and the client should re-use the same
        ``since`` next time.

        **Limitation:** soft-deletes (medications/allergies/etc.) and hard-
        deletes (examinations/documents) are NOT represented — the client must
        periodically do a full re-sync to discover deletions.

        Types (CSV): ``medications``, ``allergies``, ``vaccines``,
        ``clinical_events``, ``documents``, ``examinations``. Default: all six.
        """
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.clinical_event import ClinicalEvent
        from app.models.document_model import DocumentModel
        from app.models.examination_model import ExaminationModel
        from app.models.fhir.allergy import AllergyIntolerance
        from app.models.fhir.medication import Medication
        from app.models.fhir.vaccine import PatientImmunization

        patient_id = self._bound_patient_id(integration)
        qp = request.query_params

        # Parse `since` (default: last 7 days — bounded first-pull window).
        since_str = qp.get("since")
        if since_str:
            try:
                since = datetime.datetime.fromisoformat(
                    since_str.replace("Z", "+00:00")
                )
                if since.tzinfo is None:
                    since = since.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                raise ValueError(f"Invalid 'since' ISO 8601: {since_str}")
        else:
            since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=7
            )

        try:
            limit = min(int(qp.get("limit", "500")), 2000)
        except (TypeError, ValueError):
            limit = 500

        types_param = qp.get("types", "")
        requested = (
            {t.strip() for t in types_param.split(",") if t.strip()}
            if types_param
            else None
        )
        ALL_TYPES = (
            "medications",
            "allergies",
            "vaccines",
            "clinical_events",
            "documents",
            "examinations",
        )
        to_query = requested if requested else set(ALL_TYPES)
        unknown = to_query - set(ALL_TYPES)
        if unknown:
            raise ValueError(
                f"Unknown /changes types: {sorted(unknown)}. Valid: {list(ALL_TYPES)}"
            )

        data: dict[str, list[dict[str, Any]]] = {}
        max_updated: datetime.datetime | None = None

        async with AsyncSessionLocal() as db:
            if "medications" in to_query:
                res = await db.execute(
                    select(Medication)
                    .where(
                        Medication.tenant_id == integration.tenant_id,
                        Medication.patient_id == patient_id,
                        Medication.updated_at > since,
                        Medication.deleted_at.is_(None),
                    )
                    .order_by(Medication.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["medications"] = [
                        {
                            "id": str(m.id),
                            "updated_at": m.updated_at.isoformat()
                            if m.updated_at
                            else None,
                            "status": m.status.value
                            if hasattr(m.status, "value")
                            else m.status,
                            "code_text": (m.code or {}).get("text")
                            if isinstance(m.code, dict)
                            else None,
                            "start_date": m.start_date.isoformat()
                            if m.start_date
                            else None,
                        }
                        for m in rows
                    ]
                    max_updated = (
                        max(r.updated_at for r in rows) if rows else max_updated
                    )

            if "allergies" in to_query:
                res = await db.execute(
                    select(AllergyIntolerance)
                    .where(
                        AllergyIntolerance.tenant_id == integration.tenant_id,
                        AllergyIntolerance.patient_id == patient_id,
                        AllergyIntolerance.updated_at > since,
                        AllergyIntolerance.deleted_at.is_(None),
                    )
                    .order_by(AllergyIntolerance.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["allergies"] = [
                        {
                            "id": str(a.id),
                            "updated_at": a.updated_at.isoformat()
                            if a.updated_at
                            else None,
                            "clinical_status": a.clinical_status.value
                            if hasattr(a.clinical_status, "value")
                            else a.clinical_status,
                            "code_text": (a.code or {}).get("text")
                            if isinstance(a.code, dict)
                            else None,
                        }
                        for a in rows
                    ]
                    batch_max = max(r.updated_at for r in rows)
                    max_updated = max(max_updated or batch_max, batch_max)

            if "vaccines" in to_query:
                res = await db.execute(
                    select(PatientImmunization)
                    .where(
                        PatientImmunization.tenant_id == integration.tenant_id,
                        PatientImmunization.patient_id == patient_id,
                        PatientImmunization.updated_at > since,
                        PatientImmunization.deleted_at.is_(None),
                    )
                    .order_by(PatientImmunization.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["vaccines"] = [
                        {
                            "id": str(v.id),
                            "updated_at": v.updated_at.isoformat()
                            if v.updated_at
                            else None,
                            "status": v.status.value
                            if hasattr(v.status, "value")
                            else v.status,
                            "administered_at": v.administered_at.isoformat()
                            if v.administered_at
                            else None,
                        }
                        for v in rows
                    ]
                    batch_max = max(r.updated_at for r in rows)
                    max_updated = max(max_updated or batch_max, batch_max)

            if "clinical_events" in to_query:
                res = await db.execute(
                    select(ClinicalEvent)
                    .where(
                        ClinicalEvent.tenant_id == integration.tenant_id,
                        ClinicalEvent.patient_id == patient_id,
                        ClinicalEvent.updated_at > since,
                        ClinicalEvent.deleted_at.is_(None),
                    )
                    .order_by(ClinicalEvent.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["clinical_events"] = [
                        {
                            "id": str(e.id),
                            "updated_at": e.updated_at.isoformat()
                            if e.updated_at
                            else None,
                            "status": e.status.value
                            if hasattr(e.status, "value")
                            else e.status,
                            "title": e.title,
                            "onset_date": e.onset_date.isoformat()
                            if e.onset_date
                            else None,
                        }
                        for e in rows
                    ]
                    batch_max = max(r.updated_at for r in rows)
                    max_updated = max(max_updated or batch_max, batch_max)

            if "documents" in to_query:
                res = await db.execute(
                    select(DocumentModel)
                    .where(
                        DocumentModel.tenant_id == integration.tenant_id,
                        DocumentModel.patient_id == patient_id,
                        DocumentModel.updated_at > since,
                        DocumentModel.deleted_at.is_(None),
                    )
                    .order_by(DocumentModel.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["documents"] = [
                        {
                            "id": str(d.id),
                            "updated_at": d.updated_at.isoformat()
                            if d.updated_at
                            else None,
                            "filename": d.filename,
                            "status": d.status,
                            "examination_id": str(d.examination_id)
                            if d.examination_id
                            else None,
                        }
                        for d in rows
                    ]
                    batch_max = max(r.updated_at for r in rows)
                    max_updated = max(max_updated or batch_max, batch_max)

            if "examinations" in to_query:
                res = await db.execute(
                    select(ExaminationModel)
                    .where(
                        ExaminationModel.tenant_id == integration.tenant_id,
                        ExaminationModel.patient_id == patient_id,
                        ExaminationModel.updated_at > since,
                        ExaminationModel.deleted_at.is_(None),
                    )
                    .order_by(ExaminationModel.updated_at.asc())
                    .limit(limit)
                )
                rows = res.scalars().all()
                if rows:
                    data["examinations"] = [
                        {
                            "id": str(e.id),
                            "updated_at": e.updated_at.isoformat()
                            if e.updated_at
                            else None,
                            "examination_date": e.examination_date.isoformat()
                            if e.examination_date
                            else None,
                            "extraction_status": e.extraction_status,
                        }
                        for e in rows
                    ]
                    batch_max = max(r.updated_at for r in rows)
                    max_updated = max(max_updated or batch_max, batch_max)

        # Cursor advances only if we found at least one updated row. The client
        # uses the returned cursor as the next poll's `since`; null cursor =
        # nothing new, client should re-use the same `since`.
        cursor = max_updated.isoformat() if max_updated else None
        return {
            "data": data,
            "cursor": cursor,
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "since": since.isoformat(),
        }

    async def _delete_examination(
        self, integration: UserIntegration, exam_id: str
    ) -> dict[str, Any]:
        """`DELETE /examinations/{id}` — hard delete (matches the PWA endpoint).
        Removes all linked documents (file unlink + row delete), defensively
        bulk-deletes orphan observations + medications, then deletes the exam
        row. Patient-scoped via `_bound_examination`. Celery cumulative
        extraction is **not** re-triggered (the exam is going away)."""
        from sqlalchemy import delete, select

        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel
        from app.models.fhir import Observation
        from app.models.fhir.medication import Medication
        from app.services.document_service import delete_document

        async with AsyncSessionLocal() as db:
            exam = await self._bound_examination(db, integration, exam_id)
            # Cascade document deletion explicitly so files are unlinked; the
            # DB-level CASCADE would handle the rows but leave orphan files.
            doc_rows = (
                await db.execute(
                    select(DocumentModel.id).where(
                        DocumentModel.examination_id == exam.id,
                        DocumentModel.tenant_id == integration.tenant_id,
                    )
                )
            ).all()
            for (doc_id,) in doc_rows:
                await delete_document(str(doc_id), db, trigger_cumulative=False)
            # Defensive cleanup (the FK CASCADE handles most of this; explicit
            # in case of partial cascades or legacy rows).
            await db.execute(
                delete(Observation).where(
                    Observation.examination_id == exam.id,
                    Observation.tenant_id == integration.tenant_id,
                )
            )
            await db.execute(
                delete(Medication).where(
                    Medication.examination_id == exam.id,
                    Medication.tenant_id == integration.tenant_id,
                )
            )
            await db.delete(exam)
            await db.commit()
        return {
            "id": exam_id,
            "deleted": True,
            "message": "Examination and all related clinical data and documents deleted.",
        }

    async def _delete_document(
        self, integration: UserIntegration, doc_id: str
    ) -> dict[str, Any]:
        """`DELETE /documents/{id}` — hard delete (file unlink + row delete +
        raw-SQL observation cleanup). Patient-scoped via `_bound_document` so a
        cross-patient attempt fails *before* any side effects."""
        from app.core.database import AsyncSessionLocal
        from app.services.document_service import delete_document

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            # delete_document commits internally; the bound-document check
            # above already established patient scope.
            await delete_document(str(d.id), db, trigger_cumulative=True)
        return {"id": doc_id, "deleted": True, "message": "Document deleted."}

    async def _trigger_document_extraction(
        self, integration: UserIntegration, doc_id: str
    ) -> dict[str, Any]:
        """`POST /documents/{id}/extract` — dispatch the OCR/NLP pipeline.
        Patient-scoped via `_bound_document`. Returns the bridge-equivalent of
        the PWA endpoint shape (``{job_id, message}``). The OCR task runs
        asynchronously; poll ``GET /documents/{id}/extract/status``."""
        from app.core.database import AsyncSessionLocal
        from app.services.document_service import trigger_extraction

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            job_id = await trigger_extraction(str(d.id), db)
        return {"job_id": job_id, "message": "Extraction started."}

    async def _document_extraction_status(
        self, integration: UserIntegration, doc_id: str
    ) -> dict[str, Any]:
        """`GET /documents/{id}/extract/status` — the live OCR row state."""
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            d = await self._bound_document(db, integration, doc_id)
            return {
                "id": str(d.id),
                "status": d.status,
                "progress": d.progress,
                "error_message": d.error_message,
            }

    async def _examination_extraction_status(
        self, integration: UserIntegration, exam_id: str
    ) -> dict[str, Any]:
        """`GET /examinations/{id}/status` — the exam-level extraction state
        plus the per-document status array (mirrors the PWA endpoint)."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel

        async with AsyncSessionLocal() as db:
            e = await self._bound_examination(db, integration, exam_id)
            res = await db.execute(
                select(DocumentModel)
                .where(
                    DocumentModel.examination_id == e.id,
                    DocumentModel.tenant_id == integration.tenant_id,
                    DocumentModel.deleted_at.is_(None),
                )
                .order_by(DocumentModel.created_at.asc())
            )
            docs = [
                {
                    "id": str(d.id),
                    "filename": d.filename,
                    "status": d.status,
                    "progress": d.progress,
                    "include_in_extraction": d.include_in_extraction,
                }
                for d in res.scalars().all()
            ]
            return {
                "id": str(e.id),
                "extraction_status": e.extraction_status,
                "extraction_progress": e.extraction_progress,
                "error_message": getattr(e, "error_message", None),
                "documents": docs,
            }

    async def _extraction_logs(
        self, integration: UserIntegration, exam_id: str
    ) -> list[dict[str, Any]]:
        """`GET /examinations/{id}/logs` — the TaskLog rows for the exam and
        its documents. Patient-scoped via `_bound_examination`; the tenant
        filter on the log query is the integration's tenant."""
        from sqlalchemy import or_, select

        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel
        from app.models.task_log import TaskLog

        async with AsyncSessionLocal() as db:
            e = await self._bound_examination(db, integration, exam_id)
            doc_ids = [
                row[0]
                for row in (
                    await db.execute(
                        select(DocumentModel.id).where(
                            DocumentModel.examination_id == e.id
                        )
                    )
                ).all()
            ]
            doc_id_strs = [str(d) for d in doc_ids]
            stmt = (
                select(TaskLog)
                .where(
                    TaskLog.tenant_id == integration.tenant_id,
                    or_(
                        TaskLog.resource_id == e.id,
                        TaskLog.resource_id.in_(doc_ids) if doc_ids else False,
                        TaskLog.task_id == str(e.id),
                        TaskLog.task_id.in_(doc_id_strs) if doc_id_strs else False,
                    ),
                )
                .order_by(TaskLog.created_at.asc())
            )
            res = await db.execute(stmt)
            return [
                {
                    "id": str(log.id),
                    "task_name": log.task_name,
                    "task_id": log.task_id,
                    "resource_id": str(log.resource_id) if log.resource_id else None,
                    "level": log.level,
                    "stage": log.stage,
                    "message": log.message,
                    "data": log.data,
                    "created_at": log.created_at.isoformat()
                    if log.created_at
                    else None,
                }
                for log in res.scalars().all()
            ]

    async def _upload_document(
        self, integration: UserIntegration, exam_id: str, request: Any
    ) -> dict[str, Any]:
        """Upload a document (base64 JSON) for an examination. Idempotent via the
        existing ``(tenant, patient, integration, external_id)`` dedup: pass a
        client ``id``/``client_request_id`` and a re-upload after a network blip
        returns the same row (no re-write, no OCR re-dispatch)."""
        import base64

        from app.core.database import AsyncSessionLocal
        from app.services.document_service import ingest_document_bytes

        patient_id = self._bound_patient_id(integration)
        payload = await request.json()
        client_id = payload.get("id") or payload.get("client_request_id")
        filename = payload.get("filename") or "upload.bin"
        data_b64 = payload.get("data")
        if not data_b64:
            raise ValueError("Missing base64 'data' field.")
        try:
            content = base64.b64decode(data_b64, validate=True)
        except (TypeError, ValueError):
            raise ValueError("Invalid base64 'data'.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB cap."
            )
        include_in_extraction = bool(payload.get("include_in_extraction", False))
        async with AsyncSessionLocal() as db:
            exam = await self._bound_examination(db, integration, exam_id)
            doc = await ingest_document_bytes(
                filename=filename,
                content=content,
                content_type=payload.get("content_type"),
                tenant_id=integration.tenant_id,
                patient_id=patient_id,
                owner_id=integration.user_id,
                db=db,
                examination_id=exam.id,
                include_in_extraction=include_in_extraction,
                source_integration_id=integration.id,
                external_id=client_id,
            )
        return {
            "id": str(doc.id),
            "external_id": client_id,
            "filename": doc.filename,
            "status": doc.status,
            "progress": doc.progress,
        }

    async def _create_examination(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.fhir.organization import OrganizationModel
        from app.schemas.examination import ExaminationCreate
        from app.services.examination_service import create_examination
        from app.services.integration_actor import resolve_integration_actor

        payload_data = await request.json()
        client_id = payload_data.get("id") or payload_data.get("client_request_id")
        patient_id = self._bound_patient_id(integration)
        exam_date = None
        if payload_data.get("date"):
            try:
                exam_date = datetime.datetime.fromisoformat(
                    payload_data["date"].replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        async with AsyncSessionLocal() as db:
            org_id = None
            lab_name = payload_data.get("lab_name")
            if lab_name:
                org = (
                    await db.execute(
                        select(OrganizationModel).where(
                            OrganizationModel.tenant_id == integration.tenant_id,
                            OrganizationModel.name == lab_name,
                        )
                    )
                ).scalar_one_or_none()
                if not org:
                    org = OrganizationModel(
                        tenant_id=integration.tenant_id, name=lab_name
                    )
                    db.add(org)
                    await db.flush()
                org_id = org.id
            actor = await resolve_integration_actor(db, integration)
            payload = ExaminationCreate(
                patient_id=patient_id,
                examination_date=exam_date,
                notes=payload_data.get("notes"),
                patient_notes=payload_data.get("patient_notes"),
                category=payload_data.get("category"),
                organization_id=org_id,
                diagnoses=payload_data.get("diagnoses") or [],
                impressions=payload_data.get("impressions"),
                auto_extract_metadata=False,
                extraction_status="completed",
            )
            exam = await create_examination(
                db,
                actor,
                payload,
                source_integration_id=integration.id,
                external_id=client_id,
            )
            await db.commit()
        return {"id": str(exam.id), "external_id": client_id}

    def _parse_records(
        self,
        records: list[ClientRecord],
        builder: ObservationBuilder,
        integration_id: str,
        instance_name: str,
        examination_id: str | None = None,
    ) -> list[ObservationCreate]:
        observations = []
        for record in records:
            # The ObservationBuilder is stateful and mutated in place by every
            # set_* call (each returns ``self``). Reset per record so a
            # conditionally-set field (reference range, interpretation, value)
            # from the previous record doesn't leak into the next — the
            # documented gotcha (SDK skill §10.4).
            builder.reset()
            dt = datetime.datetime.now(datetime.timezone.utc)
            if record.timestamp:
                try:
                    dt = datetime.datetime.fromisoformat(
                        record.timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            from app.models.enums import CodingSystem

            system_map = {
                "loinc": CodingSystem.LOINC,
                "snomed": CodingSystem.SNOMED,
                "custom": CodingSystem.CUSTOM,
            }
            coding_system = system_map.get(
                record.coding_system.lower(), CodingSystem.CUSTOM
            )

            # Extract biomarker ID directly if provided
            biomarker_id = None
            if hasattr(record, "biomarker_id") and record.biomarker_id:
                try:
                    from uuid import UUID

                    biomarker_id = UUID(record.biomarker_id)
                except ValueError:
                    pass

            code_str = record.code or "unknown"

            obs_builder = builder.set_biomarker(
                code_str,
                record.name,
                coding_system=coding_system,
                biomarker_id=biomarker_id,
            ).set_effective_date(dt)

            if record.type == "quantitative" and record.value is not None:
                obs_builder.set_value(
                    record.value, record.unit or "", record.unit or ""
                )
            elif record.type == "categorical" and record.value_string:
                obs_builder.set_value_string(record.value_string)

            if record.reference_range:
                obs_builder.set_reference_range(
                    low=record.reference_range.get("low"),
                    high=record.reference_range.get("high"),
                )

            if record.interpretation:
                obs_builder.set_interpretation(record.interpretation)

            obs = obs_builder.build()

            # Ensure the performer explicitly links to this integration instance so it appears in the UI
            obs.performer = [
                {
                    "type": "Integration",
                    "display": record.performer
                    or instance_name
                    or "Health Assistant Bridge",
                    "reference": f"Integration/{integration_id}",
                }
            ]

            if examination_id:
                from uuid import UUID

                try:
                    obs.examination_id = (
                        UUID(examination_id)
                        if isinstance(examination_id, str)
                        else examination_id
                    )
                except ValueError:
                    pass

            observations.append(obs)

        return observations

    async def _handle_map_request(
        self, integration: UserIntegration, map_request: MapRequestPayload
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from app.ai.providers.service import AIProviderService
        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition

        async with AsyncSessionLocal() as db:
            # 1. Fetch existing biomarkers
            bio_defs = await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.tenant_id == integration.tenant_id
                )
            )
            existing_bios = bio_defs.scalars().all()

            catalog_str = "\n".join(
                [
                    f"ID: {b.id} | Name: {b.name} | Code: {b.code} | Aliases: {', '.join(b.aliases or [])}"
                    for b in existing_bios
                ]
            )

            # 2. Setup LLM Orchestrator
            ai_service = AIProviderService(db)
            try:
                nlp_extractor = await ai_service.get_nlp_extractor(
                    tenant_id=integration.tenant_id, user_id=integration.user_id
                )
            except Exception:  # noqa: BLE001 - any NLP failure maps to a friendly 400
                logger.error("Failed to get NLP extractor for mapping")
                raise ValueError("AI mapping service is currently unavailable.")

            # 3. Delegate to central NLP component
            try:
                result = await nlp_extractor.map_external_metrics(
                    raw_metrics=map_request.unmapped_metrics,
                    existing_catalog_str=catalog_str,
                )
                return result.model_dump()
            except NotImplementedError:
                # Re-raise NotImplementedError to be caught by the router and returned as 400
                raise
            except Exception as e:  # noqa: BLE001 - any LLM failure maps to a friendly 400
                logger.error("LLM Mapping failed: %s", e)
                if integration.is_debug_enabled:
                    try:
                        await self.log_debug_payload(
                            integration,
                            "AI Mapping Error",
                            {"error": str(e)},
                            level="error",
                        )
                    except Exception as log_err:  # noqa: BLE001 - debug logging is best-effort
                        logger.debug("Debug payload logging failed: %s", log_err)
                raise ValueError(f"Failed to perform AI mapping: {e!s}")

    async def _process_and_save_sync_data(
        self,
        integration: UserIntegration,
        sync_payload: SyncPayload,
        builder: ObservationBuilder,
    ) -> int:
        """Helper to process and save observations and examinations to DB."""
        if not sync_payload.records and not sync_payload.examinations:
            return 0

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.fhir import Observation
        from app.models.fhir.organization import OrganizationModel
        from app.models.user_integration import IntegrationSyncLog
        from app.schemas.examination import ExaminationCreate
        from app.services.examination_service import create_examination
        from app.services.fhir_service import map_observations_to_biomarkers
        from app.services.integration_actor import resolve_integration_actor
        from app.services.integration_sync_service import apply_telemetry_split

        count = 0
        start_time = datetime.datetime.now(datetime.timezone.utc)

        async with AsyncSessionLocal() as db:
            try:
                observations_data = []

                # 1. Process Examinations via the canonical service (E.2).
                # Previously the bridge inlined ~80 LOC of dedup + direct
                # ORM construction here, including a stale category field
                # name that doesn't exist on the live model (the column
                # was renamed when categories moved into the unified
                # taxonomy) — so the bridge silently dropped the category
                # on every exam it created. Routing through
                # ``examination_service.create_examination`` fixes that
                # and gets dedup + category resolution + audit provenance
                # for free. The integration actor (workstream D) gives
                # the service a TokenData to write under.
                if sync_payload.examinations:
                    actor = await resolve_integration_actor(db, integration)
                    for client_exam in sync_payload.examinations:
                        # Org resolution stays provider-side — the service
                        # handles patient / category / dedup / doctors, not
                        # organization management.
                        org_id = None
                        if client_exam.lab_name:
                            org_stmt = select(OrganizationModel).where(
                                OrganizationModel.tenant_id == integration.tenant_id,
                                OrganizationModel.name == client_exam.lab_name,
                            )
                            org = (await db.execute(org_stmt)).scalar_one_or_none()
                            if not org:
                                org = OrganizationModel(
                                    tenant_id=integration.tenant_id,
                                    name=client_exam.lab_name,
                                )
                                db.add(org)
                                await db.flush()
                            org_id = org.id

                        # Parse the upstream date string.
                        exam_date = None
                        if client_exam.date:
                            try:
                                exam_date = datetime.datetime.fromisoformat(
                                    client_exam.date.replace("Z", "+00:00")
                                ).date()
                            except ValueError:
                                pass

                        # The service handles category resolution (text →
                        # concept_id via MedicalProcessingService), dedup on
                        # (tenant, patient, source_integration_id,
                        # external_id), patient validation, and audit
                        # provenance. We just build the payload and pass
                        # source_integration_id + external_id explicitly.
                        payload = ExaminationCreate(
                            patient_id=integration.patient_id,
                            examination_date=exam_date,
                            notes=client_exam.notes,
                            patient_notes=client_exam.patient_notes,
                            category=client_exam.category,
                            organization_id=org_id,
                            diagnoses=client_exam.diagnoses or [],
                            impressions=client_exam.impressions,
                            # Bridge already has structured records —
                            # disable the LLM extraction pipeline.
                            auto_extract_metadata=False,
                            extraction_status="completed",
                        )
                        exam = await create_examination(
                            db,
                            actor,
                            payload,
                            source_integration_id=integration.id,
                            external_id=client_exam.id,
                        )

                        if client_exam.records:
                            exam_obs = self._parse_records(
                                client_exam.records,
                                builder,
                                str(integration.id),
                                integration.instance_name,
                                examination_id=str(exam.id),
                            )
                            observations_data.extend(exam_obs)

                # 2. Process Flat Records
                if sync_payload.records:
                    flat_obs = self._parse_records(
                        sync_payload.records,
                        builder,
                        str(integration.id),
                        integration.instance_name,
                    )
                    observations_data.extend(flat_obs)

                # 3. Handle all parsed observations
                observations = []
                for obs_data in observations_data:
                    obs_dict = (
                        obs_data.model_dump(exclude_unset=True)
                        if hasattr(obs_data, "model_dump")
                        else obs_data.dict(exclude_unset=True)
                        if hasattr(obs_data, "dict")
                        else obs_data
                    )
                    obs = Observation(**obs_dict)
                    observations.append(obs)

                if observations:
                    await map_observations_to_biomarkers(db, observations)

                    # Route observations through the shared FHIR/telemetry split
                    # (the same helper ``run_sync`` and the webhook endpoint
                    # use). Previously this block inlined a stale copy that
                    # constructed ``TelemetryDataModel(heart_rate=, steps=,
                    # calories=, data=)`` — kwargs removed in the long-format
                    # hypertable rewrite (migration ``t1e2l3o4n5g6``); the
                    # model now takes ``slug=, value=, unit=, patient_id=``.
                    # The inlined copy would have raised ``TypeError`` on any
                    # wearable record. The shared helper resolves biomarker
                    # defs, builds long-format rows, and assigns performers.
                    telemetry_records, fhir_records = await apply_telemetry_split(
                        db,
                        observations,
                        tenant_id=integration.tenant_id,
                        instance_name=integration.instance_name,
                        provider_name=integration.provider,
                        integration_id=integration.id,
                    )
                    if telemetry_records:
                        db.add_all(telemetry_records)
                    if fhir_records:
                        db.add_all(fhir_records)

                    count += len(telemetry_records) + len(fhir_records)

                # We do NOT db.add(integration) here because it is already attached
                # to the outer session provided by the FastAPI Dependency `Depends(get_db)`.
                # If we add it to the inner `AsyncSessionLocal()`, SQLAlchemy throws an error.
                integration.last_synced_at = datetime.datetime.now(
                    datetime.timezone.utc
                )

                sync_log = IntegrationSyncLog(
                    integration_id=integration.id,
                    tenant_id=integration.tenant_id,
                    status="success",
                    records_synced=count,
                    started_at=start_time,
                    completed_at=integration.last_synced_at,
                )
                db.add(sync_log)

                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("Error saving data from bridge: %s", e)

                if integration.is_debug_enabled:
                    try:
                        await self.log_debug_payload(
                            integration,
                            "Bridge Save Error",
                            {"error": str(e)},
                            level="error",
                        )
                    except Exception as log_err:  # noqa: BLE001 - debug logging is best-effort
                        logger.debug("Debug payload logging failed: %s", log_err)

                sync_log = IntegrationSyncLog(
                    integration_id=integration.id,
                    tenant_id=integration.tenant_id,
                    status="failed",
                    records_synced=0,
                    started_at=start_time,
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    error_message=str(e),
                )
                db.add(sync_log)
                await db.commit()
                raise

        return count

    def get_custom_actions(self) -> list[dict[str, str]]:
        return [
            {
                "id": "get_api_details",
                "label": "Connection Details",
                "style": "primary",
            },
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"},
        ]

    async def execute_custom_action(
        self, integration: UserIntegration, action_id: str, **kwargs
    ) -> dict[str, Any]:
        from integrations.sdk import code_block, kv_block

        if action_id == "get_api_details":
            api_path = f"/api/v1/integrations/{self.domain}/api/{integration.id}"
            api_url = f"{api_path}"  # relative; the host is the backend base URL
            return {
                "message": "Bridge API is ready. See the Connection Details below.",
                "results": [
                    kv_block(
                        "Connection Details",
                        {
                            "Instance ID": str(integration.id),
                            "Instance Name": integration.instance_name or "(unnamed)",
                            "Domain": self.domain,
                            "API Base Path": api_url,
                            "Status endpoint": f"{api_url}/status",
                            "Sync endpoint": f"{api_url}/sync",
                            "Map endpoint": f"{api_url}/map",
                        },
                    ),
                    code_block(
                        "Example: check status",
                        f"curl http://<backend-host>:8000{api_url}/status",
                        language="bash",
                    ),
                ],
            }

        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_timestamp", None)
            return {
                "message": "Sync cursor has been reset. The client will pull all historical data on the next sync."
            }

        raise NotImplementedError(f"Action '{action_id}' is not supported.")

    # --- Phase 6: clinical-record mutations + bound-* helpers --------------
    #
    # Each resource follows the same shape: POST creates (idempotent on a
    # client-supplied ``id``/``client_request_id`` → ``external_id``), PUT
    # updates, DELETE soft-deletes (or hard-deletes for doctors). Every
    # patient-scoped path resolves ``_bound_patient_id`` first; mutations on
    # an existing row go through a ``_bound_*`` loader that re-verifies the
    # tenant + patient before any side effect, so a cross-patient attempt
    # fails *before* the service call.

    async def _bound_medication(
        self, db: Any, integration: UserIntegration, medication_id: str
    ):
        """Load a medication scoped to the bound patient (mirrors
        ``_bound_examination``). Soft-deleted rows are excluded."""
        from uuid import UUID

        from sqlalchemy import select

        from app.models.fhir.medication import Medication

        patient_id = self._bound_patient_id(integration)
        try:
            mid = UUID(medication_id)
        except ValueError:
            raise ValueError(f"Invalid medication id: {medication_id}")
        res = await db.execute(
            select(Medication).where(
                Medication.id == mid,
                Medication.tenant_id == integration.tenant_id,
                Medication.patient_id == patient_id,
                Medication.deleted_at.is_(None),
            )
        )
        m = res.scalars().one_or_none()
        if m is None:
            raise ValueError("Medication not found for this patient.")
        return m

    async def _bound_allergy(
        self, db: Any, integration: UserIntegration, allergy_id: str
    ):
        from uuid import UUID

        from sqlalchemy import select

        from app.models.fhir.allergy import AllergyIntolerance

        patient_id = self._bound_patient_id(integration)
        try:
            aid = UUID(allergy_id)
        except ValueError:
            raise ValueError(f"Invalid allergy id: {allergy_id}")
        res = await db.execute(
            select(AllergyIntolerance).where(
                AllergyIntolerance.id == aid,
                AllergyIntolerance.tenant_id == integration.tenant_id,
                AllergyIntolerance.patient_id == patient_id,
                AllergyIntolerance.deleted_at.is_(None),
            )
        )
        a = res.scalars().one_or_none()
        if a is None:
            raise ValueError("Allergy not found for this patient.")
        return a

    async def _bound_vaccine(
        self, db: Any, integration: UserIntegration, vaccine_id: str
    ):
        from uuid import UUID

        from sqlalchemy import select

        from app.models.fhir.vaccine import PatientImmunization

        patient_id = self._bound_patient_id(integration)
        try:
            vid = UUID(vaccine_id)
        except ValueError:
            raise ValueError(f"Invalid vaccine id: {vaccine_id}")
        res = await db.execute(
            select(PatientImmunization).where(
                PatientImmunization.id == vid,
                PatientImmunization.tenant_id == integration.tenant_id,
                PatientImmunization.patient_id == patient_id,
                PatientImmunization.deleted_at.is_(None),
            )
        )
        v = res.scalars().one_or_none()
        if v is None:
            raise ValueError("Immunization not found for this patient.")
        return v

    async def _bound_doctor(
        self, db: Any, integration: UserIntegration, doctor_id: str
    ):
        """Load a tenant-scoped doctor. Doctors aren't patient-scoped — the
        bridge returns/acts on what the owner's role permits within the
        tenant."""
        from uuid import UUID

        from sqlalchemy import select

        from app.models.doctor_model import DoctorModel

        try:
            did = UUID(doctor_id)
        except ValueError:
            raise ValueError(f"Invalid doctor id: {doctor_id}")
        res = await db.execute(
            select(DoctorModel).where(
                DoctorModel.id == did,
                DoctorModel.tenant_id == integration.tenant_id,
            )
        )
        d = res.scalars().one_or_none()
        if d is None:
            raise ValueError("Doctor not found in this tenant.")
        return d

    @staticmethod
    def _medication_to_dict(m: Any) -> dict[str, Any]:
        return {
            "id": str(m.id),
            "status": m.status.value if hasattr(m.status, "value") else m.status,
            "intent": m.intent.value if hasattr(m.intent, "value") else m.intent,
            "code": m.code,
            "start_date": m.start_date.isoformat() if m.start_date else None,
            "end_date": m.end_date.isoformat() if m.end_date else None,
            "dosage": m.dosage,
            "frequency": m.frequency,
            "reason": m.reason,
            "note": m.note,
            "examination_id": str(m.examination_id) if m.examination_id else None,
            "source_integration_id": str(m.source_integration_id)
            if m.source_integration_id
            else None,
            "external_id": m.external_id,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }

    @staticmethod
    def _allergy_to_dict(a: Any) -> dict[str, Any]:
        return {
            "id": str(a.id),
            "clinical_status": a.clinical_status.value
            if hasattr(a.clinical_status, "value")
            else a.clinical_status,
            "verification_status": a.verification_status,
            "category": a.category.value if hasattr(a.category, "value") else a.category,
            "criticality": a.criticality.value
            if hasattr(a.criticality, "value")
            else a.criticality,
            "code": a.code,
            "onset_date": a.onset_date.isoformat() if a.onset_date else None,
            "resolved_date": a.resolved_date.isoformat() if a.resolved_date else None,
            "last_occurrence": a.last_occurrence.isoformat()
            if a.last_occurrence
            else None,
            "note": a.note,
            "reactions": a.reactions,
            "source_integration_id": str(a.source_integration_id)
            if a.source_integration_id
            else None,
            "external_id": a.external_id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }

    @staticmethod
    def _vaccine_to_dict(v: Any) -> dict[str, Any]:
        return {
            "id": str(v.id),
            "status": v.status.value if hasattr(v.status, "value") else v.status,
            "vaccine_code": v.vaccine_code,
            "administered_at": v.administered_at.isoformat()
            if v.administered_at
            else None,
            "dose_number": v.dose_number,
            "lot_number": v.lot_number,
            "manufacturer": v.manufacturer,
            "location": v.location,
            "note": v.note,
            "examination_id": str(v.examination_id) if v.examination_id else None,
            "source_integration_id": str(v.source_integration_id)
            if v.source_integration_id
            else None,
            "external_id": v.external_id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        }

    @staticmethod
    def _doctor_to_dict(d: Any) -> dict[str, Any]:
        return {
            "id": str(d.id),
            "name": d.name,
            "specialty_concept_id": str(d.specialty_concept_id)
            if d.specialty_concept_id
            else None,
            "specialty": d.specialty,
            "license_number": d.license_number,
            "email": d.email,
            "phone": d.phone,
            "telecom": d.telecom,
            "address": d.address,
            "office_number": d.office_number,
            "office_details": d.office_details,
        }

    async def _create_medication(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`POST /medications` — add a patient medication. Idempotent on
        ``id``/``client_request_id`` → ``external_id`` (the service enforces
        a partial unique index on ``(source_integration_id, external_id)``)."""
        from app.core.database import AsyncSessionLocal
        from app.schemas.medication import MedicationRecordCreate
        from app.services.integration_actor import resolve_integration_actor
        from app.services.medication_service import add_patient_medication

        payload = await request.json()
        client_id = payload.get("id") or payload.get("client_request_id")
        patient_id = self._bound_patient_id(integration)
        payload["patient_id"] = str(patient_id)
        try:
            data = MedicationRecordCreate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid medication payload: {e}")
        async with AsyncSessionLocal() as db:
            actor = await resolve_integration_actor(db, integration)
            m = await add_patient_medication(
                db,
                actor,
                data,
                source_integration_id=integration.id,
                external_id=client_id,
            )
        return self._medication_to_dict(m)

    async def _update_medication(
        self, integration: UserIntegration, medication_id: str, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.medication import MedicationRecordUpdate
        from app.services.medication_service import update_patient_medication

        payload = await request.json()
        try:
            data = MedicationRecordUpdate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid medication payload: {e}")
        async with AsyncSessionLocal() as db:
            m = await self._bound_medication(db, integration, medication_id)
            updated = await update_patient_medication(
                db, m.id, integration.tenant_id, data
            )
        if updated is None:
            raise ValueError("Medication not found for this patient.")
        return self._medication_to_dict(updated)

    async def _delete_medication(
        self, integration: UserIntegration, medication_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.medication_service import delete_patient_medication

        async with AsyncSessionLocal() as db:
            m = await self._bound_medication(db, integration, medication_id)
            await delete_patient_medication(db, m.id, integration.tenant_id)
        return {"id": medication_id, "deleted": True, "message": "Medication deleted."}

    async def _create_allergy(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.allergy import AllergyIntoleranceCreate
        from app.services.allergy_service import add_patient_allergy
        from app.services.integration_actor import resolve_integration_actor

        payload = await request.json()
        client_id = payload.get("id") or payload.get("client_request_id")
        patient_id = self._bound_patient_id(integration)
        payload["patient_id"] = str(patient_id)
        try:
            data = AllergyIntoleranceCreate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid allergy payload: {e}")
        async with AsyncSessionLocal() as db:
            actor = await resolve_integration_actor(db, integration)
            a = await add_patient_allergy(
                db,
                actor,
                data,
                source_integration_id=integration.id,
                external_id=client_id,
            )
        return self._allergy_to_dict(a)

    async def _update_allergy(
        self, integration: UserIntegration, allergy_id: str, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.allergy import AllergyIntoleranceUpdate
        from app.services.allergy_service import update_patient_allergy

        payload = await request.json()
        try:
            data = AllergyIntoleranceUpdate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid allergy payload: {e}")
        async with AsyncSessionLocal() as db:
            a = await self._bound_allergy(db, integration, allergy_id)
            updated = await update_patient_allergy(
                db, a.id, integration.tenant_id, data
            )
        if updated is None:
            raise ValueError("Allergy not found for this patient.")
        return self._allergy_to_dict(updated)

    async def _delete_allergy(
        self, integration: UserIntegration, allergy_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.allergy_service import delete_patient_allergy

        async with AsyncSessionLocal() as db:
            a = await self._bound_allergy(db, integration, allergy_id)
            await delete_patient_allergy(db, a.id, integration.tenant_id)
        return {"id": allergy_id, "deleted": True, "message": "Allergy deleted."}

    async def _create_vaccine(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.vaccine import PatientImmunizationCreate
        from app.services.integration_actor import resolve_integration_actor
        from app.services.vaccine_service import add_patient_immunization

        payload = await request.json()
        client_id = payload.get("id") or payload.get("client_request_id")
        patient_id = self._bound_patient_id(integration)
        payload["patient_id"] = str(patient_id)
        try:
            data = PatientImmunizationCreate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid vaccine payload: {e}")
        async with AsyncSessionLocal() as db:
            actor = await resolve_integration_actor(db, integration)
            v = await add_patient_immunization(
                db,
                actor,
                data,
                source_integration_id=integration.id,
                external_id=client_id,
            )
        return self._vaccine_to_dict(v)

    async def _update_vaccine(
        self, integration: UserIntegration, vaccine_id: str, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.vaccine import PatientImmunizationUpdate
        from app.services.vaccine_service import update_patient_immunization

        payload = await request.json()
        try:
            data = PatientImmunizationUpdate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid vaccine payload: {e}")
        async with AsyncSessionLocal() as db:
            v = await self._bound_vaccine(db, integration, vaccine_id)
            updated = await update_patient_immunization(
                db, v.id, integration.tenant_id, data
            )
        if updated is None:
            raise ValueError("Immunization not found for this patient.")
        return self._vaccine_to_dict(updated)

    async def _delete_vaccine(
        self, integration: UserIntegration, vaccine_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.vaccine_service import delete_patient_immunization

        async with AsyncSessionLocal() as db:
            v = await self._bound_vaccine(db, integration, vaccine_id)
            await delete_patient_immunization(db, v.id, integration.tenant_id)
        return {"id": vaccine_id, "deleted": True, "message": "Immunization deleted."}

    async def _create_clinical_event(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`POST /clinical-events` — create a clinical event. Returns the
        service's ``to_dict()`` (fully eager-loaded). Idempotent on
        ``id``/``client_request_id`` → ``external_id``."""
        from app.core.database import AsyncSessionLocal
        from app.schemas.clinical_event import ClinicalEventCreate
        from app.services.clinical_event_service import create_event
        from app.services.integration_actor import resolve_integration_actor

        payload = await request.json()
        client_id = payload.get("id") or payload.get("client_request_id")
        patient_id = self._bound_patient_id(integration)
        payload["patient_id"] = str(patient_id)
        try:
            data = ClinicalEventCreate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid clinical event payload: {e}")
        async with AsyncSessionLocal() as db:
            actor = await resolve_integration_actor(db, integration)
            result = await create_event(
                db,
                actor,
                data,
                source_integration_id=integration.id,
                external_id=client_id,
            )
        return result

    async def _update_clinical_event(
        self, integration: UserIntegration, event_id: str, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.schemas.clinical_event import ClinicalEventUpdate
        from app.services.clinical_event_service import update_event
        from app.services.integration_actor import resolve_integration_actor

        payload = await request.json()
        try:
            data = ClinicalEventUpdate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid clinical event payload: {e}")
        async with AsyncSessionLocal() as db:
            await self._bound_clinical_event(db, integration, event_id)
            actor = await resolve_integration_actor(db, integration)
            result = await update_event(db, event_id, actor, data)
        return result

    async def _delete_clinical_event(
        self, integration: UserIntegration, event_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.clinical_event_service import soft_delete_event
        from app.services.integration_actor import resolve_integration_actor

        async with AsyncSessionLocal() as db:
            await self._bound_clinical_event(db, integration, event_id)
            actor = await resolve_integration_actor(db, integration)
            await soft_delete_event(db, event_id, actor)
        return {
            "id": event_id,
            "deleted": True,
            "message": "Clinical event deleted.",
        }

    async def _add_clinical_event_occurrence(
        self, integration: UserIntegration, event_id: str, request: Any
    ) -> dict[str, Any]:
        """`POST /clinical-events/{id}/occurrences` — log a recurrence."""
        from app.core.database import AsyncSessionLocal
        from app.schemas.clinical_event import ClinicalEventOccurrenceCreate
        from app.services.clinical_event_service import add_occurrence
        from app.services.integration_actor import resolve_integration_actor

        payload = await request.json()
        try:
            data = ClinicalEventOccurrenceCreate(**payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid occurrence payload: {e}")
        async with AsyncSessionLocal() as db:
            await self._bound_clinical_event(db, integration, event_id)
            actor = await resolve_integration_actor(db, integration)
            result = await add_occurrence(db, event_id, actor, data)
        return result

    async def _create_doctor(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`POST /doctors` — add a tenant-scoped doctor. The service takes
        individual kwargs (no Pydantic schema); the bridge forwards the
        recognized fields from the JSON payload."""
        from app.core.database import AsyncSessionLocal
        from app.services.doctor_service import create_doctor

        payload = await request.json()
        name = payload.get("name")
        if not name:
            raise ValueError("Doctor 'name' is required.")
        async with AsyncSessionLocal() as db:
            d = await create_doctor(
                integration.tenant_id,
                integration.user_id,
                name,
                specialty=payload.get("specialty"),
                license_number=payload.get("license_number"),
                email=payload.get("email"),
                phone=payload.get("phone"),
                telecom=payload.get("telecom"),
                address=payload.get("address"),
                office_number=payload.get("office_number"),
                office_details=payload.get("office_details"),
                user_id=integration.user_id,
                db=db,
            )
        return self._doctor_to_dict(d)

    async def _update_doctor(
        self, integration: UserIntegration, doctor_id: str, request: Any
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.doctor_service import update_doctor

        payload = await request.json()
        allowed = (
            "name",
            "specialty",
            "license_number",
            "email",
            "phone",
            "telecom",
            "address",
            "office_number",
            "office_details",
        )
        kwargs = {k: v for k, v in payload.items() if k in allowed}
        if not kwargs:
            raise ValueError("No updatable fields supplied.")
        async with AsyncSessionLocal() as db:
            await self._bound_doctor(db, integration, doctor_id)
            updated = await update_doctor(
                doctor_id, integration.tenant_id, db, **kwargs
            )
        if updated is None:
            raise ValueError("Doctor not found in this tenant.")
        return self._doctor_to_dict(updated)

    async def _delete_doctor(
        self, integration: UserIntegration, doctor_id: str
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.doctor_service import delete_doctor

        async with AsyncSessionLocal() as db:
            await self._bound_doctor(db, integration, doctor_id)
            await delete_doctor(doctor_id, integration.tenant_id, db)
        return {"id": doctor_id, "deleted": True, "message": "Doctor deleted."}

    # --- Phase 7: notification inbox + preferences ------------------------
    #
    # Notifications are addressed to the integration's *owner* (the user who
    # onboarded the connection), NOT to the bound patient — a single user
    # owns multiple patients (their child, an elderly parent), and the inbox
    # is one-per-user. Every handler therefore keys on ``integration.user_id``
    # + ``integration.tenant_id``; ``_bound_patient_id`` is intentionally
    # NOT called here.
    #
    # The fan-out side (emit / push dispatch) is reused unchanged — these
    # handlers are pure readers + mutators on top of the existing services.

    async def _notification_inbox(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /notifications/inbox` — the owner's inbox. Optional filters
        mirror the PWA: ``?status=``, ``?category=``, ``?source=``,
        ``?patient_id=``, ``?limit=`` (default 50), ``?offset=``."""
        from app.core.database import AsyncSessionLocal
        from app.services.notification_service import get_inbox

        qp = request.query_params
        try:
            limit = min(int(qp.get("limit", "50")), 200)
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = max(int(qp.get("offset", "0")), 0)
        except (TypeError, ValueError):
            offset = 0
        patient_id = qp.get("patient_id")
        if patient_id:
            try:
                from uuid import UUID

                patient_id = UUID(patient_id)
            except ValueError:
                raise ValueError(f"Invalid patient_id: {patient_id}")
        async with AsyncSessionLocal() as db:
            items, total = await get_inbox(
                integration.user_id,
                integration.tenant_id,
                status=qp.get("status"),
                category=qp.get("category"),
                source=qp.get("source"),
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
        return self._read_envelope(
            items, cursor=str(offset + limit) if offset + limit < total else None
        ) | {"total": total}

    async def _notification_unread_count(
        self, integration: UserIntegration
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.notification_service import get_unread_count

        async with AsyncSessionLocal() as db:
            count = await get_unread_count(
                integration.user_id, integration.tenant_id
            )
        return {"unread_count": count}

    async def _notification_read_all(
        self, integration: UserIntegration
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.notification_service import mark_all_read

        async with AsyncSessionLocal() as db:
            n = await mark_all_read(integration.user_id, integration.tenant_id)
        return {"status": "success", "marked_read": n}

    async def _notification_mark(
        self,
        integration: UserIntegration,
        recipient_id: str,
        action: str,
    ) -> dict[str, Any]:
        """`PATCH /notifications/{recipient_id}/read|dismiss`."""
        from app.core.database import AsyncSessionLocal
        from app.services.notification_service import (
            mark_dismissed,
            mark_read,
        )

        from uuid import UUID

        try:
            rid = UUID(recipient_id)
        except ValueError:
            raise ValueError(f"Invalid recipient id: {recipient_id}")
        async with AsyncSessionLocal() as db:
            ok = (
                await mark_read(rid, integration.user_id, integration.tenant_id)
                if action == "read"
                else await mark_dismissed(
                    rid, integration.user_id, integration.tenant_id
                )
            )
        if not ok:
            raise ValueError(
                f"Notification recipient {recipient_id} not found for this user."
            )
        return {"status": "success"}

    async def _notification_preferences(
        self, integration: UserIntegration
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.notification_preferences_service import (
            NotificationPreferencesService,
        )

        async with AsyncSessionLocal() as db:
            svc = NotificationPreferencesService(db)
            items = await svc.get_all(
                integration.user_id, integration.tenant_id
            )
        return self._read_envelope(items)

    async def _notification_set_preference(
        self,
        integration: UserIntegration,
        kind_id: str,
        request: Any,
    ) -> dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from app.services.notification_preferences_service import (
            NotificationPreferencesService,
        )

        payload = await request.json()
        enabled = payload.get("enabled")
        if enabled is None:
            raise ValueError("'enabled' (boolean) is required.")
        async with AsyncSessionLocal() as db:
            svc = NotificationPreferencesService(db)
            meta = await svc.set(
                integration.user_id,
                integration.tenant_id,
                kind_id,
                bool(enabled),
            )
        return {
            "status": "success",
            "kind_id": kind_id,
            "enabled": bool(enabled),
            "label": getattr(meta, "label", None),
            "mutable": getattr(meta, "mutable", None),
        }

    async def _notification_list_triggers(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`GET /notifications/triggers` — biomarker-threshold rules for the
        bound patient. (Scheduled medication reminders live on the device in
        the mobile app's WorkManager; the server-side rule surface is for
        "alert me when biomarker X crosses Y".)"""
        from app.services.notification_rule_service import list_rules

        patient_id = self._bound_patient_id(integration)
        qp = request.query_params
        try:
            limit = min(int(qp.get("limit", "100")), 500)
        except (TypeError, ValueError):
            limit = 100
        try:
            offset = max(int(qp.get("offset", "0")), 0)
        except (TypeError, ValueError):
            offset = 0
        rules = await list_rules(
            integration.tenant_id,
            patient_id=patient_id,
            limit=limit,
            offset=offset,
        )
        items = [
            {
                "id": str(r.id),
                "rule_type": r.rule_type,
                "biomarker_id": str(r.biomarker_id) if r.biomarker_id else None,
                "operator": r.operator,
                "value": r.value,
                "severity": r.severity,
                "enabled": r.enabled,
                "cooldown_minutes": r.cooldown_minutes,
                "title_template": r.title_template,
                "body_template": r.body_template,
                "created_at": r.created_at.isoformat()
                if r.created_at
                else None,
                "updated_at": r.updated_at.isoformat()
                if r.updated_at
                else None,
            }
            for r in rules
        ]
        return self._read_envelope(items)

    async def _notification_create_trigger(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        from app.services.notification_rule_service import create_rule

        patient_id = self._bound_patient_id(integration)
        payload = await request.json()
        payload["patient_id"] = str(patient_id)
        if not payload.get("rule_type"):
            raise ValueError("'rule_type' is required.")
        rule = await create_rule(payload, integration.tenant_id)
        if rule is None:
            raise ValueError("Could not create trigger (invalid payload).")
        return {
            "id": str(rule.id),
            "rule_type": rule.rule_type,
            "biomarker_id": str(rule.biomarker_id) if rule.biomarker_id else None,
            "operator": rule.operator,
            "value": rule.value,
            "enabled": rule.enabled,
        }

    async def _notification_delete_trigger(
        self, integration: UserIntegration, trigger_id: str
    ) -> dict[str, Any]:
        from uuid import UUID

        from app.services.notification_rule_service import delete_rule

        try:
            tid = UUID(trigger_id)
        except ValueError:
            raise ValueError(f"Invalid trigger id: {trigger_id}")
        ok = await delete_rule(tid, integration.tenant_id)
        if not ok:
            raise ValueError(
                f"Trigger {trigger_id} not found in this tenant."
            )
        return {"id": trigger_id, "deleted": True, "message": "Trigger deleted."}

    # --- Phase 8: native push device registration -------------------------

    async def _register_push_device(
        self, integration: UserIntegration, request: Any
    ) -> dict[str, Any]:
        """`POST /notifications/register-device` — register / re-register the
        calling mobile install for native push. Owner-scoped (the integration
        owner receives the push, not the bound patient — multi-patient
        guardians get one inbox per owner).

        Payload:
        - ``device_id`` (str, required) — client-generated stable per-install id.
        - ``platform`` (``unifiedpush`` | ``fcm``, required).
        - ``endpoint_url`` (str, required) — UnifiedPush distributor endpoint
          OR the FCM registration token.
        - ``encryption_pubkey`` (str, optional) — HPKE public key for E2E
          encryption (reserved; v2 ships plaintext-over-HTTPS).
        - ``app_version``, ``user_agent`` (optional) — for the device list.

        Re-registering the same ``(user, device)`` upserts (e.g. when the
        user picks a new UnifiedPush distributor). The row is therefore
        stable across app upgrades.
        """
        from app.services.mobile_push_service import register_device

        payload = await request.json()
        device_id = payload.get("device_id")
        platform = payload.get("platform")
        endpoint_url = payload.get("endpoint_url")
        if not device_id:
            raise ValueError("'device_id' is required.")
        if not platform:
            raise ValueError("'platform' is required ('unifiedpush' or 'fcm').")
        if not endpoint_url:
            raise ValueError("'endpoint_url' is required.")

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            target = await register_device(
                db,
                user_id=integration.user_id,
                tenant_id=integration.tenant_id,
                device_id=device_id,
                platform=platform,
                endpoint_url=endpoint_url,
                encryption_pubkey=payload.get("encryption_pubkey"),
                app_version=payload.get("app_version"),
                user_agent=payload.get("user_agent"),
            )
        return target.to_dict()

    async def _unregister_push_device(
        self, integration: UserIntegration, device_id: str
    ) -> dict[str, Any]:
        """`DELETE /notifications/register-device/{device_id}` — soft-deactivate
        a device (sign-out / lost-device). Hard-delete is not exposed; the row
        is retained so a re-registration of the same device id is detected."""
        from app.core.database import AsyncSessionLocal
        from app.services.mobile_push_service import unregister_device

        async with AsyncSessionLocal() as db:
            ok = await unregister_device(
                db,
                user_id=integration.user_id,
                device_id=device_id,
            )
        if not ok:
            raise ValueError(
                f"Device '{device_id}' not found for this user (or already inactive)."
            )
        return {
            "device_id": device_id,
            "deleted": True,
            "message": "Device unregistered.",
        }

    async def _list_push_devices(
        self, integration: UserIntegration
    ) -> dict[str, Any]:
        """`GET /devices` — the 'Where am I signed in' list. Endpoint URLs
        are masked (the dispatch task reads the raw column; the bridge never
        echoes a usable credential back)."""
        from app.core.database import AsyncSessionLocal
        from app.services.mobile_push_service import list_devices

        async with AsyncSessionLocal() as db:
            targets = await list_devices(
                db, user_id=integration.user_id, include_inactive=False
            )
        return self._read_envelope([t.to_dict() for t in targets])

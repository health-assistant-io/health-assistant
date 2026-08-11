import datetime
import logging
from typing import Any
from uuid import UUID

from app.ai.schemas.nlp import MetricMappingRequest
from app.models.user_integration import UserIntegration
from app.schemas.fhir.observation import ObservationCreate
from pydantic import BaseModel, Field

from integrations.sdk import BaseHealthProvider
from integrations.sdk.observation_builder import ObservationBuilder

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
        elif path.startswith("examinations/") and method == "GET":
            parts = path.split("/")
            if len(parts) == 2:
                return await self._read_examination_detail(integration, parts[1])
            if len(parts) == 3 and parts[2] == "documents":
                return await self._list_documents(integration, parts[1], request)
            raise NotImplementedError(
                f"GET /{path} is not supported by the bridge API."
            )
        elif path.startswith("examinations/") and method == "POST":
            parts = path.split("/")
            if len(parts) == 3 and parts[2] == "documents":
                return await self._upload_document(integration, parts[1], request)
            raise NotImplementedError(
                f"POST /{path} is not supported by the bridge API."
            )
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
        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition
        from app.services.fhir_service import list_observations_latest
        from app.services.telemetry_service import get_patient_telemetry_latest
        from sqlalchemy import or_, select

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
        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition
        from sqlalchemy import or_, select

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
        from app.core.database import AsyncSessionLocal
        from app.models.examination_model import ExaminationModel
        from sqlalchemy import select

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

        from app.models.examination_model import ExaminationModel
        from sqlalchemy import select

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
        from app.core.database import AsyncSessionLocal
        from app.models.document_model import DocumentModel
        from sqlalchemy import select

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
            items = [
                {
                    "id": str(d.id),
                    "filename": d.filename,
                    "status": d.status,
                    "progress": d.progress,
                    "external_id": d.external_id,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in res.scalars().all()
            ]
        return self._read_envelope(items)

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
        from app.core.database import AsyncSessionLocal
        from app.models.fhir.organization import OrganizationModel
        from app.schemas.examination import ExaminationCreate
        from app.services.examination_service import create_examination
        from app.services.integration_actor import resolve_integration_actor
        from sqlalchemy import select

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
        from app.ai.providers.service import AIProviderService
        from app.core.database import AsyncSessionLocal
        from app.models.biomarker_model import BiomarkerDefinition
        from sqlalchemy import select

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

        from app.core.database import AsyncSessionLocal
        from app.models.fhir import Observation
        from app.models.fhir.organization import OrganizationModel
        from app.models.user_integration import IntegrationSyncLog
        from app.schemas.examination import ExaminationCreate
        from app.services.examination_service import create_examination
        from app.services.fhir_service import map_observations_to_biomarkers
        from app.services.integration_actor import resolve_integration_actor
        from app.services.integration_sync_service import apply_telemetry_split
        from sqlalchemy import select

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

# ruff: noqa: E501 -- long example/payload literals; the generator stays readable beside them
#!/usr/bin/env python3
"""Export the health-assistant-bridge sub-API schema + cross-SDK fixtures.

Plan 32 ADR-0040 D11 (Phase 6 close): the bridge goes schema-first. This
script EXTRACTS the served API surface from the provider's live route
table, payload models and frame builders — never restates it — and renders
the committed schema artifact (``app/extensions/builtin/
health_assistant_bridge/schema/bridge-api.schema.json``) plus the
cross-SDK fixture wall (``schema/fixtures.json``).

Extraction sources (all live code):
  - ``provider._ROUTES``            → routes, methods, path templates
  - pydantic models the handlers parse / dump → request/response components
  - ``provider.BRIDGE_FEATURES``    → the feature-flag handshake vocabulary
  - ``BridgeError(...)`` literals   → the closed typed error vocabulary
  - ``query_params.get(...)``       → per-route query parameter names
  - ``ai/flow_events*.py`` builders → the SSE stream event vocabulary + frames
  - ``ai/assistance/turns.py``      → SSE transport facts (headers, keep-alive,
                                      the flow_failed error-code classification)

Usage (career's check-openapi.py precedent — regenerate + --check in the
same commit as the change, --check is the CI drift gate):
  uv run python scripts/export_bridge_schema.py --update   # deliberate change
  uv run python scripts/export_bridge_schema.py --check    # CI default

Run from ``backend/`` (needs the app import path, no database).
"""

from __future__ import annotations

import argparse
import ast
import copy
import difflib
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "app" / "extensions" / "builtin" / "health_assistant_bridge"
SCHEMA_DIR = BRIDGE_DIR / "schema"
SCHEMA_FILE = SCHEMA_DIR / "bridge-api.schema.json"
FIXTURES_FILE = SCHEMA_DIR / "fixtures.json"
SCHEMA_VERSION = 1

sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _load_provider():
    from app.extensions.builtin.health_assistant_bridge import provider as mod

    return mod


def _route_entries(routes):
    """Reflect ``_ROUTES`` into (method, template, pattern, handler, params)."""
    out = []
    for method, pattern, handler_name in routes:
        if isinstance(pattern, str):
            template, params = pattern, []
        else:
            raw = pattern.pattern
            params = list(pattern.groupindex.keys())
            template = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", raw)
            template = template.removeprefix("^").removesuffix("$")
        out.append(
            (
                method,
                template,
                raw if not isinstance(pattern, str) else pattern,
                handler_name,
                params,
            )
        )
    return out


def _pydantic_models():
    """The pydantic payload models, by name, from their real homes."""
    from pydantic import BaseModel

    from app.ai.schemas.nlp import MappedMetric, MapResponsePayload, MetricMappingRequest
    from app.extensions.builtin.health_assistant_bridge.provider import (
        ClientExaminationRecord,
        ClientRecord,
        MapRequestPayload,
        SyncPayload,
    )
    from app.schemas.allergy import AllergyIntoleranceCreate, AllergyIntoleranceUpdate
    from app.schemas.clinical_event import (
        ClinicalEventCreate,
        ClinicalEventOccurrenceCreate,
        ClinicalEventUpdate,
    )
    from app.schemas.examination import ExaminationCreate
    from app.schemas.medication import MedicationRecordCreate, MedicationRecordUpdate
    from app.schemas.vaccine import PatientImmunizationCreate, PatientImmunizationUpdate

    models = {
        MapRequestPayload,
        MetricMappingRequest,
        MapResponsePayload,
        MappedMetric,
        SyncPayload,
        ClientRecord,
        ClientExaminationRecord,
        MedicationRecordCreate,
        MedicationRecordUpdate,
        AllergyIntoleranceCreate,
        AllergyIntoleranceUpdate,
        PatientImmunizationCreate,
        PatientImmunizationUpdate,
        ClinicalEventCreate,
        ClinicalEventUpdate,
        ClinicalEventOccurrenceCreate,
        ExaminationCreate,
    }
    assert all(issubclass(m, BaseModel) for m in models)
    return {m.__name__: m for m in sorted(models, key=lambda m: m.__name__)}


def _handler_model_bindings(provider_cls):
    """AST-bind each handler to the pydantic models it parses.

    Walks every ``_``-prefixed method of the provider class, resolves
    ``Model(**payload)`` call names against the module globals and the
    handler's own function-level imports, and keeps the names that are
    pydantic models. The binding is derived, never declared.
    """
    import pydantic

    src = inspect.getsourcefile(provider_cls)
    tree = ast.parse(Path(src).read_text())
    module_models = {
        name
        for name, obj in vars(_load_provider()).items()
        if isinstance(obj, type) and issubclass(obj, pydantic.BaseModel)
    }
    bindings: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = set(module_models)
            for sub in ast.walk(fn):
                if isinstance(sub, ast.ImportFrom):
                    for alias in sub.names:
                        try:
                            mod = importlib.import_module(sub.module)
                            obj = getattr(mod, alias.name)
                            if isinstance(obj, type) and issubclass(obj, pydantic.BaseModel):
                                local.add(alias.asname or alias.name)
                        except (ImportError, AttributeError):
                            pass
            found = set()
            for sub in ast.walk(fn):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id in local
                ):
                    found.add(sub.func.id)
            if found:
                bindings[fn.name] = found
    return bindings


def _extracted_components():
    """Render the pydantic models as flat components. Nested ``$defs`` (enum /
    sub-model vocabulary the models reference) are hoisted into top-level
    components — transitively, so every ``#/components/...`` ref resolves."""
    extracted = {}
    pending = []
    for name, model in _pydantic_models().items():
        schema = model.model_json_schema(ref_template="#/components/{model}")
        schema.pop("title", None)
        schema["x-extracted"] = True
        extracted[name] = schema
        pending.append(schema)
    while pending:
        schema = pending.pop()
        for defname, defschema in schema.pop("$defs", {}).items():
            defschema.pop("title", None)
            if defname in extracted:
                prior = {
                    k: v for k, v in extracted[defname].items() if k not in ("x-extracted", "$defs")
                }
                if prior != defschema:
                    raise AssertionError(f"nested schema name collision: {defname}")
                continue
            extracted[defname] = defschema
            pending.append(defschema)
    return extracted


def _handler_query_params(provider_cls):
    """Extract per-handler query parameter NAMES from ``.get("name"...)``."""
    src = inspect.getsourcefile(provider_cls)
    tree = ast.parse(Path(src).read_text())
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qp_aliases = set()
            for sub in ast.walk(fn):
                if (
                    isinstance(sub, ast.Assign)
                    and isinstance(sub.value, ast.Attribute)
                    and sub.value.attr == "query_params"
                    and isinstance(sub.value.value, ast.Name)
                    and sub.value.value.id == "request"
                ):
                    qp_aliases |= {t.id for t in sub.targets if isinstance(t, ast.Name)}
            names = []
            for sub in ast.walk(fn):
                if not (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                ):
                    continue
                target = sub.func.value
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "query_params"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "request"
                ) or (isinstance(target, ast.Name) and target.id in qp_aliases):
                    names.append(sub.args[0].value)
            if names:
                out[fn.name] = sorted(set(names))
    return out


def _bridge_error_vocabulary(provider_cls):
    """The live ``BridgeError(status, code, ...)`` vocabulary from the AST."""
    src = inspect.getsourcefile(provider_cls)
    tree = ast.parse(Path(src).read_text())
    codes: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BridgeError"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[1], ast.Constant)
        ):
            status, code = node.args[0].value, node.args[1].value
            codes.setdefault(code, [])
            if status not in codes[code]:
                codes[code].append(status)
    return {code: sorted(statuses) for code, statuses in sorted(codes.items())}


# ---------------------------------------------------------------------------
# Authored components (dict-builder-backed shapes; coverage-gated by route
# assembly — every route must resolve, every entry must be routed)
# ---------------------------------------------------------------------------


def _obj(properties, required=None, description=None, example=None):
    out = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    if description:
        out["description"] = description
    if example is not None:
        out["examples"] = [example]
    return out


def _open(description=None):
    out = {"type": "object"}
    if description:
        out["description"] = description
    return out


def _arr(items, description=None):
    out = {"type": "array", "items": items}
    if description:
        out["description"] = description
    return out


def _s(description=None):
    out = {"type": "string"}
    if description:
        out["description"] = description
    return out


def _nullable(schema):
    if "$ref" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    out = dict(schema)
    out["type"] = [out["type"], "null"] if isinstance(out["type"], str) else out["type"]
    return out


def _authored_components(extracted):
    """The wire shapes the provider builds inline (dict builders / literals).

    Authored once from the handler sources, then pinned by the fixture wall
    and re-reviewed on every generator --check failure (a route/model change
    fails CI until this table and the examples are consciously updated).
    """
    ex = extracted

    def merge_create(model_name):
        """Bridge create-request shape: the extracted schema plus the
        idempotency aliases, with the server-injected patient_id removed."""
        base = copy.deepcopy(ex[model_name])
        props = base.get("properties", {})
        props.pop("patient_id", None)
        props.pop("external_id", None)
        base["required"] = [
            r for r in base.get("required", []) if r not in ("patient_id", "external_id")
        ]
        props["id"] = _s(
            "Idempotency key — persisted as external_id; a retry with the same id never duplicates"
        )
        props["client_request_id"] = _s("Alias of id")
        base["properties"] = props
        base["description"] = (
            f"{model_name} as the bridge accepts it (id/client_request_id aliased to external_id; patient_id pinned server-side to the bound patient)"
        )
        base["x-extracted"] = "app.schemas base + bridge idempotency envelope"
        return base

    c: dict[str, dict] = {}

    # --- status handshake -------------------------------------------------
    c["BridgeStatus"] = _obj(
        {
            "status": _s(),
            "server_time": _nullable(
                {
                    "type": "integer",
                    "description": "Epoch seconds; lets a skewed client resync before signing (replay window ±5 min)",
                }
            ),
            "integration_id": _nullable(_s()),
            "last_synced_at": _nullable(_s("ISO 8601")),
            "cursor": _nullable(_s()),
            "latest_sdks": _nullable(
                _obj(
                    {"python": _s(), "ts": _s(), "kotlin": _s()},
                    description="Served from the bridge manifest's sdks block; empty object when absent",
                )
            ),
            "frontend_base_url": _nullable(_s()),
            "server_version": _nullable(_s()),
            "bridge_features": _arr(
                _s(), "Closed feature-flag vocabulary — see top-level handshake.featureFlags"
            ),
        },
        required=["status"],
        example={
            "status": "active",
            "integration_id": "6f1a2b3c-4d5e-4f60-a7b8-c9d0e1f2a3b4",
            "last_synced_at": "2026-10-16T09:24:00+00:00",
            "cursor": "2026-10-16T09:24:00+00:00",
            "latest_sdks": {"python": "1.4.0", "ts": "1.4.0", "kotlin": "0.6.0"},
            "frontend_base_url": "http://localhost:3000",
            "server_version": "1.7.0",
            "bridge_features": ["exam_enrichment", "ai_assistant", "ai_streaming"],
        },
    )
    c["UnsignedStatus"] = _obj(
        {"status": _s(), "server_time": {"type": "integer", "description": "Epoch seconds"}},
        required=["status", "server_time"],
        description="The tokenless liveness probe answer (pre-pairing; leaks nothing else)",
        example={"status": "active", "server_time": 1794936000},
    )

    # --- read envelope ----------------------------------------------------
    c["ReadEnvelope"] = _obj(
        {
            "data": _arr({}),
            "cursor": _nullable(_s("Opaque pagination/continuation cursor; per-route semantics")),
            "cached_at": _s("ISO 8601 read timestamp"),
        },
        required=["data", "cached_at"],
        description="The universal read-path envelope; data items are the route's responseItem shape",
    )

    # --- map / sync -------------------------------------------------------
    c["ReferenceRange"] = _obj(
        {"low": _nullable({"type": "number"}), "high": _nullable({"type": "number"})},
        description="Flat {low, high} reference range (the bridge flattens the FHIR list shape on every read)",
        example={"low": 60.0, "high": 100.0},
    )
    c["SyncResponse"] = _obj(
        {
            "success": {"type": "boolean"},
            "metrics_synced": _nullable({"type": "integer"}),
            "message": _nullable(_s()),
            "error": _nullable(
                _s("Present on the success:false failure answer (the sync handler never raises)")
            ),
        },
        required=["success"],
        example={"success": True, "metrics_synced": 3, "message": "Data synchronized successfully"},
    )

    # --- AI ---------------------------------------------------------------
    c["ChatTurnRequest"] = _obj(
        {
            "message": _s("Non-blank, ≤ 8000 chars"),
            "session_id": _s("Optional; must belong to the bound patient"),
        },
        required=["message"],
        example={
            "message": "How did my sleep change this week?",
            "session_id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
        },
    )
    c["ChatTurnResult"] = _obj(
        {
            "message": _nullable(_s()),
            "session_id": _nullable(_s()),
            "success": {"type": "boolean"},
            "message_id": _nullable(
                _s("The persisted assistant row; null on the max-iterations path")
            ),
            "tasks": _arr(
                _open("HITL proposal card (ADR-0015)"), "Render without a messages refetch"
            ),
            "citations": _arr(_open()),
        },
        required=["message", "session_id", "success", "message_id", "tasks", "citations"],
        example={
            "message": "Your average sleep moved from 6.9h to 7.4h.",
            "session_id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
            "success": True,
            "message_id": "b0a1b2c3-4d5e-4f60-8273-9a5b6c7d8e9f",
            "tasks": [],
            "citations": [],
        },
    )
    c["ChatMessage"] = _obj(
        {
            "id": _s(),
            "role": _nullable(_s()),
            "content": _open("{text: …}"),
            "tasks": _arr(_open("HITL proposal card")),
            "citations": _arr(_open()),
            "created_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "b0a1b2c3-4d5e-4f60-8273-9a5b6c7d8e9f",
            "role": "assistant",
            "content": {"text": "Your average sleep moved from 6.9h to 7.4h."},
            "tasks": [],
            "citations": [],
            "created_at": "2026-10-16T09:30:00+00:00",
        },
    )
    c["ChatSessionSummary"] = _obj(
        {
            "id": _s(),
            "title": _nullable(_s()),
            "patient_id": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
            "title": "Sleep",
            "patient_id": "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
            "updated_at": "2026-10-16T09:30:00+00:00",
        },
    )
    c["SessionRenameRequest"] = _obj(
        {"title": _s("Non-blank, ≤ 200 chars")},
        required=["title"],
        example={"title": "Sleep follow-up"},
    )
    c["SessionRenameResult"] = _obj(
        {"id": _s(), "title": _nullable(_s()), "updated_at": _nullable(_s())},
        required=["id"],
        example={
            "id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
            "title": "Sleep follow-up",
            "updated_at": "2026-10-16T09:31:00+00:00",
        },
    )
    c["SuccessAck"] = _obj(
        {"success": {"type": "boolean"}},
        required=["success"],
        example={"success": True},
    )
    c["ResumeRequest"] = _obj(
        {
            "message_id": _s(
                "Selects which assistant message's resolved tasks are summarized; omitted = most recent task-bearing message"
            )
        },
        example={"message_id": "b0a1b2c3-4d5e-4f60-8273-9a5b6c7d8e9f"},
    )
    c["ResolveTaskRequest"] = _obj(
        {
            "status": {"type": "string", "enum": ["confirmed", "dismissed"]},
            "result": _open("Optional outcome payload after the REST commit"),
            "error": _nullable(_s()),
            "final_payload": _open(),
        },
        required=["status"],
        example={"status": "confirmed", "result": {"dose": "500mg"}},
    )
    c["ResolveTaskResult"] = _obj(
        {"success": {"type": "boolean"}, "task": _open("The recorded task row")},
        required=["success", "task"],
        example={
            "success": True,
            "task": {"id": "t1a2b3c4-5d6e-4071-8293-b4c5d6e7f8a9", "status": "confirmed"},
        },
    )
    c["TranscribeRequest"] = _obj(
        {
            "data": _s("base64 audio bytes"),
            "mime": _s(
                "Full mime allowed (audio/webm;codecs=opus); normalized to the bare subtype before the allowlist check"
            ),
        },
        required=["data"],
        example={"data": "T2N0ZXRlU3RyZWFt", "mime": "audio/webm;codecs=opus"},
    )
    c["TranscriptionResult"] = _obj(
        {"text": _nullable(_s()), "success": {"type": "boolean"}},
        required=["text", "success"],
        example={"text": "What was my blood pressure yesterday?", "success": True},
    )

    # --- examinations / documents ------------------------------------------
    c["ExaminationCreateRequest"] = _obj(
        {
            "id": _s("Idempotency key (e.g. myhealth reportId) — persisted as external_id"),
            "client_request_id": _s("Alias of id"),
            "date": _s("ISO 8601 result date"),
            "lab_name": _s("Resolved/created as the organization"),
            "notes": _nullable(_s()),
            "patient_notes": _nullable(_s()),
            "category": _s("e.g. Blood Test"),
            "diagnoses": _arr(_s()),
            "impressions": _nullable(_s()),
        },
        example={
            "id": "report-8841",
            "date": "2026-09-30T08:15:00+00:00",
            "lab_name": "Meridian Labs",
            "category": "Blood Test",
            "diagnoses": ["Iron deficiency"],
            "notes": "Fasting panel",
        },
    )
    c["ExaminationCreated"] = _obj(
        {"id": _s(), "external_id": _nullable(_s())},
        required=["id"],
        example={"id": "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f9a0", "external_id": "report-8841"},
    )
    c["ExaminationSummary"] = _obj(
        {
            "id": _s(),
            "examination_date": _nullable(_s()),
            "notes": _nullable(_s()),
            "patient_notes": _nullable(_s()),
            "extraction_status": _nullable(_s()),
            "extraction_progress": _nullable({"type": "integer"}),
            "diagnoses": _arr(_s()),
            "category": _nullable(_s()),
            "lab_name": _nullable(_s()),
            "observation_count": {"type": "integer"},
            "medication_count": {"type": "integer"},
            "document_count": {"type": "integer"},
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f9a0",
            "examination_date": "2026-09-30T00:00:00+00:00",
            "notes": "Fasting panel",
            "patient_notes": None,
            "extraction_status": "completed",
            "extraction_progress": 100,
            "diagnoses": ["Iron deficiency"],
            "category": "Blood Test",
            "lab_name": "Meridian Labs",
            "observation_count": 4,
            "medication_count": 0,
            "document_count": 1,
            "created_at": "2026-09-30T08:16:00+00:00",
            "updated_at": "2026-09-30T08:20:00+00:00",
        },
    )
    c["ExaminationObservation"] = _obj(
        {
            "id": _nullable(_s()),
            "effective_datetime": _nullable(_s()),
            "code": c.get("CodeableConcept") or _open("FHIR {coding:[{system,code}],text}"),
            "raw_value": _nullable({"type": "number"}),
            "normalized_value": _nullable({"type": "number"}),
            "normalized_unit": _nullable(_s()),
            "value_string": _nullable(_s()),
            "reference_range": _nullable({"$ref": "#/components/ReferenceRange"}),
            "interpretation": _nullable(_s()),
            "relative_score": _nullable(
                {
                    "type": "number",
                    "description": "(value-low)/(high-low) clamped to [0,1]; 0.5 when the range is incomplete",
                }
            ),
            "biomarker_id": _nullable(_s()),
            "biomarker_slug": _nullable(_s()),
            "biomarker_value_type": _nullable(_s()),
            "biomarker_reference_range_min": _nullable({"type": "number"}),
            "biomarker_reference_range_max": _nullable({"type": "number"}),
            "document_id": _nullable(_s()),
        },
        example={
            "id": "3c4d5e6f-7081-4293-a4b5-c6d7e8f9a0b1",
            "effective_datetime": "2026-09-30T08:15:00+00:00",
            "code": {
                "coding": [{"system": "http://loinc.org", "code": "2711-0"}],
                "text": "Serum Iron",
            },
            "raw_value": 62.0,
            "normalized_value": 62.0,
            "normalized_unit": "ug/dL",
            "value_string": None,
            "reference_range": {"low": 60.0, "high": 170.0},
            "interpretation": None,
            "relative_score": 0.018181818181818184,
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "biomarker_slug": "serum_iron",
            "biomarker_value_type": "quantitative",
            "biomarker_reference_range_min": 60.0,
            "biomarker_reference_range_max": 170.0,
            "document_id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
        },
    )
    c["ExaminationClinicalEvent"] = _obj(
        {
            "id": _nullable(_s()),
            "title": _nullable(_s()),
            "status": _nullable(_s()),
            "reason": _nullable(_s()),
        },
        example={
            "id": "6f708192-a3b4-45c6-87d8-e9f0a1b2c3d4",
            "title": "Iron-deficiency workup",
            "status": "active",
            "reason": "Low serum iron",
        },
    )
    c["ExaminationDetail"] = _obj(
        {
            **c["ExaminationSummary"]["properties"],
            "impressions": _nullable(_s()),
            "error_message": _nullable(_s()),
            "external_id": _nullable(_s()),
            "observations": _arr({"$ref": "#/components/ExaminationObservation"}),
            "doctors": _arr({"$ref": "#/components/Doctor"}),
            "medications": _arr({"$ref": "#/components/Medication"}),
            "clinical_events": _arr({"$ref": "#/components/ExaminationClinicalEvent"}),
        },
        required=["id"],
        description="The summary card fields plus the results/doctors/medications/journeys sections (sections and counts both exclude soft-deleted rows)",
    )
    c["DocumentSummary"] = _obj(
        {
            "id": _s(),
            "filename": _nullable(_s()),
            "status": _nullable(_s()),
            "progress": _nullable({"type": "integer"}),
            "external_id": _nullable(_s()),
            "created_at": _nullable(_s()),
            "content_type": _nullable(
                _s("MIME guessed from the filename; null when the file is missing")
            ),
            "file_size": _nullable(
                {"type": "integer", "description": "Bytes on disk; null when the file is missing"}
            ),
            "examination_id": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "filename": "panel-2026-09-30.pdf",
            "status": "completed",
            "progress": 100,
            "external_id": "report-8841",
            "created_at": "2026-09-30T08:16:00+00:00",
            "content_type": "application/pdf",
            "file_size": 482133,
            "examination_id": "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f9a0",
        },
    )
    c["DocumentUploadRequest"] = _obj(
        {
            "id": _s("Idempotency key — a re-upload returns the same row, no OCR re-dispatch"),
            "client_request_id": _s("Alias of id"),
            "filename": _s("Defaults to upload.bin"),
            "data": _s("base64 payload; 25 MiB cap"),
            "content_type": _s(),
            "include_in_extraction": {"type": "boolean"},
        },
        required=["data"],
        example={
            "id": "report-8841",
            "filename": "panel-2026-09-30.pdf",
            "data": "JVBERi0xLjQK",
            "content_type": "application/pdf",
            "include_in_extraction": True,
        },
    )
    c["DocumentUploadResult"] = _obj(
        {
            "id": _s(),
            "external_id": _nullable(_s()),
            "filename": _nullable(_s()),
            "status": _nullable(_s()),
            "progress": _nullable({"type": "integer"}),
        },
        required=["id"],
        example={
            "id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "external_id": "report-8841",
            "filename": "panel-2026-09-30.pdf",
            "status": "pending",
            "progress": 0,
        },
    )
    c["DocumentText"] = _obj(
        {
            "id": _s(),
            "extracted_text": _nullable(_s("OCR Markdown, capped at 512 KiB")),
            "status": _nullable(_s()),
            "truncated": {"type": "boolean"},
        },
        required=["id", "truncated"],
        example={
            "id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "extracted_text": "# Hematology\n\nWBC 6.2 …",
            "status": "completed",
            "truncated": False,
        },
    )
    c["ExtractionJob"] = _obj(
        {"job_id": _s(), "message": _s()},
        required=["job_id", "message"],
        example={
            "job_id": "7a8b9c0d-1e2f-4031-8273-84a5b6c7d8e1",
            "message": "Extraction started.",
        },
    )
    c["DocumentExtractionStatus"] = _obj(
        {
            "id": _s(),
            "status": _nullable(_s()),
            "progress": _nullable({"type": "integer"}),
            "error_message": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "status": "completed",
            "progress": 100,
            "error_message": None,
        },
    )
    c["ExamDocumentStatus"] = _obj(
        {
            "id": _s(),
            "filename": _nullable(_s()),
            "status": _nullable(_s()),
            "progress": _nullable({"type": "integer"}),
            "include_in_extraction": {"type": "boolean"},
        },
        required=["id"],
        example={
            "id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "filename": "panel-2026-09-30.pdf",
            "status": "completed",
            "progress": 100,
            "include_in_extraction": True,
        },
    )
    c["ExamExtractionStatus"] = _obj(
        {
            "id": _s(),
            "extraction_status": _nullable(_s()),
            "extraction_progress": _nullable({"type": "integer"}),
            "error_message": _nullable(_s()),
            "documents": _arr({"$ref": "#/components/ExamDocumentStatus"}),
        },
        required=["id"],
        example={
            "id": "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f9a0",
            "extraction_status": "completed",
            "extraction_progress": 100,
            "error_message": None,
            "documents": [],
        },
    )
    c["ExtractionLogEntry"] = _obj(
        {
            "id": _s(),
            "task_name": _nullable(_s()),
            "task_id": _nullable(_s()),
            "resource_id": _nullable(_s()),
            "level": _nullable(_s()),
            "stage": _nullable(_s()),
            "message": _nullable(_s()),
            "data": _open(),
            "created_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "8b9c0d1e-2f30-4142-93a4-b5c6d7e8f9a1",
            "task_name": "extract_document",
            "task_id": "8b9c0d1e-2f30-4142-93a4-b5c6d7e8f9a1",
            "resource_id": "5e6f7081-92a3-44b5-86c7-d8e9f0a1b2c3",
            "level": "INFO",
            "stage": "ocr",
            "message": "page 1/2",
            "data": {},
            "created_at": "2026-09-30T08:17:00+00:00",
        },
    )

    # --- clinical reads -----------------------------------------------------
    c["CodeableConcept"] = _obj(
        {
            "coding": _arr(_obj({"system": _s(), "code": _s(), "display": _s()})),
            "text": _nullable(_s()),
        },
        example={
            "coding": [{"system": "http://loinc.org", "code": "2711-0"}],
            "text": "Serum Iron",
        },
    )
    c["Quantity"] = _obj(
        {
            "value": _nullable({"type": "number"}),
            "unit": _nullable(_s()),
            "system": _nullable(_s()),
            "code": _nullable(_s()),
        },
        example={"value": 62.0, "unit": "ug/dL"},
    )
    c["ObservationPoint"] = _obj(
        {
            "id": _nullable(_s()),
            "tenant_id": _nullable(_s()),
            "status": _nullable(_s()),
            "category": _nullable(_s()),
            "code": _nullable({"$ref": "#/components/CodeableConcept"}),
            "subject": _nullable(_obj({"reference": _s()})),
            "effective_datetime": _nullable(_s()),
            "value_quantity": _nullable({"$ref": "#/components/Quantity"}),
            "value_string": _nullable(_s()),
            "value_codeable_concept": _nullable({"$ref": "#/components/CodeableConcept"}),
            "reference_range": _nullable({"$ref": "#/components/ReferenceRange"}),
            "interpretation": _nullable(_s()),
            "component": _nullable(_arr(_open())),
            "comment": _nullable(_s()),
            "performer": _nullable(_arr(_open())),
            "biomarker_id": _nullable(_s()),
            "biomarker_slug": _nullable(_s()),
            "biomarker_info": _nullable(_s()),
            "biomarker_aliases": _arr(_s()),
            "biomarker_value_type": _nullable(_s()),
            "biomarker_supports_multi_state": {"type": "boolean"},
            "biomarker_reference_range_min": _nullable({"type": "number"}),
            "biomarker_reference_range_max": _nullable({"type": "number"}),
            "raw_value": _nullable({"type": "number"}),
            "normalized_value": _nullable({"type": "number"}),
            "lab_reference_range": _nullable(_s()),
            "normalized_unit": _nullable(_s()),
            "relative_score": _nullable({"type": "number"}),
            "method": _nullable(_s()),
            "examination_id": _nullable(_s()),
            "document_id": _nullable(_s()),
            "patient_id": _nullable(_s()),
        },
        description="One merged read row — FHIR and telemetry normalize to this same shape (field-for-field Observation.to_dict projection)",
        example={
            "id": "3c4d5e6f-7081-4293-a4b5-c6d7e8f9a0b1",
            "tenant_id": "9d8c7b6a-5f4e-4031-82d3-c2b1a0998877",
            "status": "final",
            "category": None,
            "code": {
                "coding": [{"system": "http://loinc.org", "code": "2711-0"}],
                "text": "Serum Iron",
            },
            "subject": {"reference": "Patient/1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9"},
            "effective_datetime": "2026-09-30T08:15:00+00:00",
            "value_quantity": {"value": 62.0, "unit": "ug/dL"},
            "value_string": None,
            "value_codeable_concept": None,
            "reference_range": {"low": 60.0, "high": 170.0},
            "interpretation": None,
            "component": None,
            "comment": None,
            "performer": [
                {
                    "type": "Integration",
                    "display": "Phone bridge",
                    "reference": "Integration/6f1a2b3c-4d5e-4f60-a7b8-c9d0e1f2a3b4",
                }
            ],
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "biomarker_slug": "serum_iron",
            "biomarker_info": None,
            "biomarker_aliases": [],
            "biomarker_value_type": "quantitative",
            "biomarker_supports_multi_state": False,
            "biomarker_reference_range_min": 60.0,
            "biomarker_reference_range_max": 170.0,
            "raw_value": 62.0,
            "normalized_value": 62.0,
            "lab_reference_range": None,
            "normalized_unit": "ug/dL",
            "relative_score": 0.018181818181818184,
            "method": None,
            "examination_id": None,
            "document_id": None,
            "patient_id": "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
        },
    )
    c["BiomarkerSummary"] = _obj(
        {
            "id": _s(),
            "name": _s(),
            "slug": _nullable(_s()),
            "code": _nullable(_s()),
            "coding_system": _nullable(_s()),
            "unit": _nullable(_s("The preferred unit's symbol")),
            "is_telemetry": {"type": "boolean"},
            "reference_range_min": _nullable({"type": "number"}),
            "reference_range_max": _nullable({"type": "number"}),
            "value_type": _nullable(_s()),
            "info": _nullable(_s("Markdown education text")),
        },
        required=["id", "name"],
        example={
            "id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "name": "Serum Iron",
            "slug": "serum_iron",
            "code": "2711-0",
            "coding_system": "loinc",
            "unit": "ug/dL",
            "is_telemetry": False,
            "reference_range_min": 60.0,
            "reference_range_max": 170.0,
            "value_type": "quantitative",
            "info": "Serum iron reflects …",
        },
    )
    c["Medication"] = _obj(
        {
            "id": _s(),
            "status": _nullable(_s()),
            "intent": _nullable(_s()),
            "code": _open("FHIR CodeableConcept"),
            "start_date": _nullable(_s()),
            "end_date": _nullable(_s()),
            "dosage": _nullable(_s()),
            "frequency": _open(),
            "reason": _nullable(_s()),
            "note": _nullable(_s()),
            "examination_id": _nullable(_s()),
            "source_integration_id": _nullable(_s()),
            "external_id": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "9c0d1e2f-3041-4253-86b7-c8d9e0f1a2b3",
            "status": "active",
            "intent": "order",
            "code": {"text": "Ferrous sulfate"},
            "start_date": "2026-10-01T00:00:00+00:00",
            "end_date": None,
            "dosage": "1 tablet daily",
            "frequency": {"repeat": {"per": 1, "unit": "d"}},
            "reason": "Iron deficiency",
            "note": None,
            "examination_id": "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f9a0",
            "source_integration_id": "6f1a2b3c-4d5e-4f60-a7b8-c9d0e1f2a3b4",
            "external_id": "med-21",
            "created_at": "2026-10-01T10:00:00+00:00",
            "updated_at": "2026-10-01T10:00:00+00:00",
        },
    )
    c["Allergy"] = _obj(
        {
            "id": _s(),
            "clinical_status": _nullable(_s()),
            "verification_status": _nullable(_s()),
            "category": _nullable(_s()),
            "criticality": _nullable(_s()),
            "code": _open("FHIR CodeableConcept"),
            "onset_date": _nullable(_s()),
            "resolved_date": _nullable(_s()),
            "last_occurrence": _nullable(_s()),
            "note": _nullable(_s()),
            "reactions": _arr(_open("FHIR Reaction")),
            "source_integration_id": _nullable(_s()),
            "external_id": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "ad1e2f30-4152-4364-87c8-d9e0f1a2b3c4",
            "clinical_status": "ACTIVE",
            "verification_status": "confirmed",
            "category": "medication",
            "criticality": "low",
            "code": {"text": "Penicillin"},
            "onset_date": "2019-03-02T00:00:00+00:00",
            "resolved_date": None,
            "last_occurrence": None,
            "note": None,
            "reactions": [],
            "source_integration_id": None,
            "external_id": None,
            "created_at": "2026-10-01T10:00:00+00:00",
            "updated_at": "2026-10-01T10:00:00+00:00",
        },
    )
    c["Vaccine"] = _obj(
        {
            "id": _s(),
            "status": _nullable(_s()),
            "vaccine_code": _open("FHIR CodeableConcept"),
            "administered_at": _nullable(_s()),
            "dose_number": _nullable(_s()),
            "lot_number": _nullable(_s()),
            "manufacturer": _nullable(_s()),
            "location": _nullable(_s()),
            "note": _nullable(_s()),
            "examination_id": _nullable(_s()),
            "source_integration_id": _nullable(_s()),
            "external_id": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "be2f3041-5263-4475-98d9-eaf012b3c4d5",
            "status": "completed",
            "vaccine_code": {"text": "Influenza vaccine"},
            "administered_at": "2025-10-05T00:00:00+00:00",
            "dose_number": "1",
            "lot_number": "FLU2025-7",
            "manufacturer": "Generic Bio",
            "location": "Left arm",
            "note": None,
            "examination_id": None,
            "source_integration_id": None,
            "external_id": None,
            "created_at": "2025-10-05T09:00:00+00:00",
            "updated_at": "2025-10-05T09:00:00+00:00",
        },
    )
    c["ClinicalEventSummary"] = _obj(
        {
            "id": _s(),
            "patient_id": _nullable(_s()),
            "tenant_id": _nullable(_s()),
            "type_id": _nullable(_s()),
            "type_name": _nullable(_s()),
            "type_slug": _nullable(_s()),
            "type_icon": _nullable(_s()),
            "type_color": _nullable(_s()),
            "status": _nullable(_s()),
            "title": _nullable(_s()),
            "description": _nullable(_s()),
            "onset_date": _nullable(_s()),
            "resolved_date": _nullable(_s()),
            "coding_system": _nullable(_s()),
            "code": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id"],
        example={
            "id": "6f708192-a3b4-45c6-87d8-e9f0a1b2c3d4",
            "patient_id": "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
            "tenant_id": "9d8c7b6a-5f4e-4031-82d3-c2b1a0998877",
            "type_id": "cf314253-6475-4686-97e8-fa0122c3d4e5",
            "type_name": "Condition",
            "type_slug": "condition",
            "type_icon": "stethoscope",
            "type_color": "#2563eb",
            "status": "active",
            "title": "Iron-deficiency workup",
            "description": None,
            "onset_date": "2026-09-30T00:00:00+00:00",
            "resolved_date": None,
            "coding_system": "custom",
            "code": None,
            "created_at": "2026-09-30T08:20:00+00:00",
            "updated_at": "2026-09-30T08:20:00+00:00",
        },
    )
    c["ClinicalEventDetail"] = _obj(
        {
            **c["ClinicalEventSummary"]["properties"],
            "type_details": _nullable(_open("The type entity's to_dict()")),
            "occurrences": _arr(_open("Serialized occurrence rows")),
            "event_metadata": _open(),
            "schedule_kind": _nullable(_s("Rendering hint resolved from the type blueprint")),
            "source_integration_id": _nullable(_s()),
            "external_id": _nullable(_s()),
            "examinations": _arr(
                _obj(
                    {
                        "id": _s(),
                        "examination_date": _nullable(_s()),
                        "notes": _nullable(_s()),
                        "reason": _nullable(_s()),
                        "examination_id": _s(),
                    }
                )
            ),
            "observations": _arr(_open("Linked observation to_dict + notes/observation_id")),
            "anatomy_links": _arr(_open()),
        },
        required=["id"],
        description="The model's full to_dict() — type_details/occurrences/examinations/observations/anatomy_links included",
    )
    c["Doctor"] = _obj(
        {
            "id": _s(),
            "name": _s(),
            "specialty_concept_id": _nullable(_s()),
            "specialty": _nullable(_s()),
            "license_number": _nullable(_s()),
            "email": _nullable(_s()),
            "phone": _nullable(_s()),
            "telecom": _arr(_open("FHIR ContactPoint")),
            "address": _arr(_open("FHIR Address")),
            "office_number": _nullable(_s()),
            "office_details": _nullable(_s()),
        },
        required=["id", "name"],
        example={
            "id": "d0415263-7485-4986-a9e9-fb0123c4d5e6",
            "name": "Dr. Amara Osei",
            "specialty_concept_id": None,
            "specialty": "Hematology",
            "license_number": "H-88421",
            "email": "a.osei@meridian.example",
            "phone": "+1-555-0142",
            "telecom": [],
            "address": [],
            "office_number": "3B",
            "office_details": "Meridian Clinics, floor 3",
        },
    )
    c["DoctorCreateRequest"] = _obj(
        {
            "name": _s("Required"),
            "specialty": _s(),
            "license_number": _s(),
            "email": _s(),
            "phone": _s(),
            "telecom": _arr(_open()),
            "address": _arr(_open()),
            "office_number": _s(),
            "office_details": _s(),
        },
        required=["name"],
        example={
            "name": "Dr. Amara Osei",
            "specialty": "Hematology",
            "email": "a.osei@meridian.example",
        },
    )
    c["DoctorUpdateRequest"] = {
        **c["DoctorCreateRequest"],
        "required": [],
        "description": "At least one updatable field; others are ignored",
    }
    c["DeleteResult"] = _obj(
        {"id": _nullable(_s()), "deleted": {"type": "boolean"}, "message": _nullable(_s())},
        example={
            "id": "9c0d1e2f-3041-4253-86b7-c8d9e0f1a2b3",
            "deleted": True,
            "message": "Medication deleted.",
        },
    )
    c["ChangesResponse"] = _obj(
        {
            "data": _obj(
                {
                    "medications": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "status": _nullable(_s()),
                                "code_text": _nullable(_s()),
                                "start_date": _nullable(_s()),
                            }
                        )
                    ),
                    "allergies": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "clinical_status": _nullable(_s()),
                                "code_text": _nullable(_s()),
                            }
                        )
                    ),
                    "vaccines": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "status": _nullable(_s()),
                                "administered_at": _nullable(_s()),
                            }
                        )
                    ),
                    "clinical_events": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "status": _nullable(_s()),
                                "title": _nullable(_s()),
                                "onset_date": _nullable(_s()),
                            }
                        )
                    ),
                    "documents": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "filename": _nullable(_s()),
                                "status": _nullable(_s()),
                                "examination_id": _nullable(_s()),
                            }
                        )
                    ),
                    "examinations": _arr(
                        _obj(
                            {
                                "id": _s(),
                                "updated_at": _s(),
                                "examination_date": _nullable(_s()),
                                "extraction_status": _nullable(_s()),
                            }
                        )
                    ),
                },
                description="Only requested types with changes appear",
            ),
            "cursor": _nullable(
                _s("max(updated_at) across the batch — null means re-use the same since")
            ),
            "cached_at": _s(),
            "since": _s("The effective window start (default: last 7 days)"),
        },
        required=["data", "cached_at", "since"],
        description="Unified delta for incremental sync; soft/hard DELETES are not represented — clients must full-resync periodically",
        example={
            "data": {
                "medications": [
                    {
                        "id": "9c0d1e2f-3041-4253-86b7-c8d9e0f1a2b3",
                        "updated_at": "2026-10-01T10:00:00+00:00",
                        "status": "active",
                        "code_text": "Ferrous sulfate",
                        "start_date": "2026-10-01T00:00:00+00:00",
                    }
                ]
            },
            "cursor": "2026-10-01T10:00:00+00:00",
            "cached_at": "2026-10-01T10:05:00+00:00",
            "since": "2026-09-24T10:05:00+00:00",
        },
    )

    # --- notifications ------------------------------------------------------
    c["NotificationEnvelope"] = _obj(
        {
            "id": _s(),
            "patient_id": _nullable(_s()),
            "trigger_id": _nullable(_s()),
            "communication_id": _nullable(_s()),
            "source": _s(),
            "type": _s(),
            "category": _s(),
            "severity": _s(),
            "title": _s(),
            "body": _nullable(_s()),
            "payload": _open(),
            "source_ref": _nullable(_s()),
            "created_at": _nullable(_s()),
        },
        required=["id", "title"],
        description="The immutable notification event (model to_dict; open to additional fields)",
        example={
            "id": "e1526374-8596-4a07-bfa1-3245c6d7e8f6",
            "patient_id": "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9",
            "trigger_id": None,
            "communication_id": None,
            "source": "rule",
            "type": "biomarker_threshold",
            "category": "clinical",
            "severity": "info",
            "title": "Serum iron recovered",
            "body": "Your latest value is back in range.",
            "payload": {},
            "source_ref": None,
            "created_at": "2026-10-16T09:00:00+00:00",
        },
    )
    c["NotificationItem"] = _obj(
        {
            "recipient_id": _s(),
            "status": _s("unread | read | dismissed"),
            "read_at": _nullable(_s()),
            "dismissed_at": _nullable(_s()),
            "notification": {"$ref": "#/components/NotificationEnvelope"},
        },
        required=["recipient_id"],
    )
    c["NotificationItem"]["examples"] = [
        {
            "recipient_id": "f2637485-9607-4b18-8ab2-4356d7e8f9a7",
            "status": "unread",
            "read_at": None,
            "dismissed_at": None,
            "notification": c["NotificationEnvelope"]["examples"][0],
        }
    ]
    c["InboxResponse"] = _obj(
        {
            "data": _arr({"$ref": "#/components/NotificationItem"}),
            "cursor": _nullable(_s("Next offset when more pages exist")),
            "cached_at": _s(),
            "total": {"type": "integer"},
        },
        required=["data", "cached_at", "total"],
    )
    c["InboxResponse"]["examples"] = [
        {
            "data": [c["NotificationItem"]["examples"][0]],
            "cursor": None,
            "cached_at": "2026-10-16T09:05:00+00:00",
            "total": 1,
        }
    ]
    c["UnreadCount"] = _obj(
        {"unread_count": {"type": "integer"}},
        required=["unread_count"],
        example={"unread_count": 3},
    )
    c["ReadAllResult"] = _obj(
        {"status": _s(), "marked_read": {"type": "integer"}},
        required=["status", "marked_read"],
        example={"status": "success", "marked_read": 3},
    )
    c["StatusAck"] = _obj(
        {"status": _s()},
        required=["status"],
        example={"status": "success"},
    )
    c["NotificationKind"] = _obj(
        {
            "kind_id": _s(),
            "label": _s(),
            "group": _s(),
            "manage_url": _s(),
            "mutable": {"type": "boolean"},
            "default_enabled": {"type": "boolean"},
            "enabled": {"type": "boolean", "description": "The user's effective state"},
        },
        required=["kind_id", "label"],
        example={
            "kind_id": "source:rule",
            "label": "Threshold alerts",
            "group": "rules",
            "manage_url": "/settings/notifications",
            "mutable": True,
            "default_enabled": True,
            "enabled": True,
        },
    )
    c["PreferenceToggleRequest"] = _obj(
        {
            "enabled": {
                "type": "boolean",
                "description": "Required; disabling an immutable kind is a typed 400",
            }
        },
        required=["enabled"],
        example={"enabled": False},
    )
    c["PreferenceToggleResult"] = _obj(
        {
            "status": _s(),
            "kind_id": _s(),
            "enabled": {"type": "boolean"},
            "label": _nullable(_s()),
            "mutable": _nullable({"type": "boolean"}),
        },
        required=["status", "kind_id", "enabled"],
        example={
            "status": "success",
            "kind_id": "source:rule",
            "enabled": False,
            "label": "Threshold alerts",
            "mutable": True,
        },
    )
    c["NotificationTrigger"] = _obj(
        {
            "id": _s(),
            "rule_type": _s(),
            "biomarker_id": _nullable(_s()),
            "operator": _nullable(_s()),
            "value": _nullable({"type": "number"}),
            "severity": _nullable(_s()),
            "enabled": {"type": "boolean"},
            "cooldown_minutes": _nullable({"type": "integer"}),
            "title_template": _nullable(_s()),
            "body_template": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id", "rule_type"],
        example={
            "id": "03748596-a718-4c29-8bc3-5467e8f9a0b8",
            "rule_type": "biomarker_threshold",
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "operator": "lt",
            "value": 60.0,
            "severity": "warning",
            "enabled": True,
            "cooldown_minutes": 720,
            "title_template": "Serum iron low",
            "body_template": "Latest value {value}",
            "created_at": "2026-10-01T10:00:00+00:00",
            "updated_at": "2026-10-01T10:00:00+00:00",
        },
    )
    c["TriggerCreateRequest"] = _obj(
        {
            "rule_type": _s("Required"),
            "biomarker_id": _s(),
            "operator": _s(),
            "value": {"type": "number"},
            "severity": _s(),
            "enabled": {"type": "boolean"},
            "cooldown_minutes": {"type": "integer"},
            "title_template": _s(),
            "body_template": _s(),
        },
        required=["rule_type"],
        example={
            "rule_type": "biomarker_threshold",
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "operator": "lt",
            "value": 60.0,
            "severity": "warning",
            "cooldown_minutes": 720,
        },
    )
    c["TriggerCreateResult"] = _obj(
        {
            "id": _s(),
            "rule_type": _s(),
            "biomarker_id": _nullable(_s()),
            "operator": _nullable(_s()),
            "value": _nullable({"type": "number"}),
            "enabled": {"type": "boolean"},
        },
        required=["id", "rule_type"],
        example={
            "id": "03748596-a718-4c29-8bc3-5467e8f9a0b8",
            "rule_type": "biomarker_threshold",
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "operator": "lt",
            "value": 60.0,
            "enabled": True,
        },
    )
    c["DeviceRegistrationRequest"] = _obj(
        {
            "device_id": _s("Client-generated stable per-install id; re-registering upserts"),
            "platform": {"type": "string", "enum": ["unifiedpush", "fcm"]},
            "endpoint_url": _s("UnifiedPush distributor endpoint or the FCM registration token"),
            "encryption_pubkey": _s("Optional HPKE public key (reserved)"),
            "app_version": _s(),
            "user_agent": _s(),
        },
        required=["device_id", "platform", "endpoint_url"],
        example={
            "device_id": "install-7f2a",
            "platform": "unifiedpush",
            "endpoint_url": "https://push.example/endpoint/abc",
            "app_version": "2.1.0",
            "user_agent": "HealthAssistant-Android/2.1.0",
        },
    )
    c["MobilePushTarget"] = _obj(
        {
            "id": _s(),
            "user_id": _s(),
            "device_id": _s(),
            "platform": _s(),
            "endpoint_url": _s("MASKED (12-char prefix + …) — never reusable as a delivery target"),
            "encryption_pubkey": _nullable(_s('"set" when present, else null')),
            "app_version": _nullable(_s()),
            "user_agent": _nullable(_s()),
            "is_active": {"type": "boolean"},
            "last_seen_at": _nullable(_s()),
            "created_at": _nullable(_s()),
            "updated_at": _nullable(_s()),
        },
        required=["id", "device_id", "platform", "endpoint_url"],
        example={
            "id": "148596a7-b829-4d3a-9cd4-6578f9a0b1c9",
            "user_id": "2596a7b8-c93a-4e4b-8de5-7689a0b1c2da",
            "device_id": "install-7f2a",
            "platform": "unifiedpush",
            "endpoint_url": "https://push…",
            "encryption_pubkey": None,
            "app_version": "2.1.0",
            "user_agent": "HealthAssistant-Android/2.1.0",
            "is_active": True,
            "last_seen_at": "2026-10-16T09:00:00+00:00",
            "created_at": "2026-10-01T10:00:00+00:00",
            "updated_at": "2026-10-16T09:00:00+00:00",
        },
    )
    c["DeviceDeleteResult"] = _obj(
        {"device_id": _s(), "deleted": {"type": "boolean"}, "message": _nullable(_s())},
        example={"device_id": "install-7f2a", "deleted": True, "message": "Device unregistered."},
    )
    c["SessionListResponse"] = _obj(
        {"data": _arr({"$ref": "#/components/ChatSessionSummary"})},
        required=["data"],
        example=None,
    )
    c["SessionListResponse"]["examples"] = [{"data": [c["ChatSessionSummary"]["examples"][0]]}]
    c["MessagesResponse"] = _obj(
        {"data": _arr({"$ref": "#/components/ChatMessage"})},
        required=["data"],
        example=None,
    )
    c["MessagesResponse"]["examples"] = [{"data": [c["ChatMessage"]["examples"][0]]}]

    # --- stream event frames (shapes validated against the real builders) ----
    c["StreamFrame"] = _obj(
        {"event": _s("The closed event name — see sse.eventNames")},
        required=["event"],
        description="Union discriminator: every stream frame is one JSON object carried as `data: <json>`, keyed by `event`",
    )
    for name, comp in _stream_frame_components().items():
        c[name] = comp

    # Request components composed from the extracted schemas.
    for name, comp in (
        ("MedicationCreateRequest", merge_create("MedicationRecordCreate")),
        ("AllergyCreateRequest", merge_create("AllergyIntoleranceCreate")),
        ("VaccineCreateRequest", merge_create("PatientImmunizationCreate")),
        ("ClinicalEventCreateRequest", merge_create("ClinicalEventCreate")),
    ):
        c[name] = comp
    for src_name, comp_name in (
        ("MedicationRecordUpdate", "MedicationUpdateRequest"),
        ("AllergyIntoleranceUpdate", "AllergyUpdateRequest"),
        ("PatientImmunizationUpdate", "VaccineUpdateRequest"),
        ("ClinicalEventUpdate", "ClinicalEventUpdateRequest"),
        ("ClinicalEventOccurrenceCreate", "ClinicalEventOccurrenceRequest"),
        ("MapRequestPayload", "MapRequest"),
        ("MetricMappingRequest", "MetricMappingRequest"),
        ("MappedMetric", "MappedMetric"),
        ("MapResponsePayload", "MapResponse"),
        ("SyncPayload", "SyncRequest"),
        ("ClientRecord", "ClientRecord"),
        ("ClientExaminationRecord", "ClientExaminationRecord"),
    ):
        comp = copy.deepcopy(ex[src_name])
        comp.setdefault("description", f"Extracted from the live payload model {src_name}")
        c[comp_name] = comp

    for name, comp in ex.items():
        c.setdefault(name, comp)

    return c


def _stream_frame_components():
    """One component per served stream event — shape-checked against the real
    builders at generation time (see _stream_frames)."""

    def frame(name, props, required, desc, example):
        return _obj(props, required=required, description=desc, example=example)

    return {
        "FlowStartedFrame": frame(
            "flow_started",
            {"event": _s(), "flow": _s(), "run_id": _s(), "steps": _arr(_open())},
            ["event", "flow"],
            "Family core",
            {"event": "flow_started", "flow": "chat", "run_id": "r-1"},
        ),
        "NodeStartedFrame": frame(
            "node_started",
            {"event": _s(), "node": _s()},
            ["event", "node"],
            "Family core",
            {"event": "node_started", "node": "agent"},
        ),
        "NodeFinishedFrame": frame(
            "node_finished",
            {"event": _s(), "node": _s(), "outcome": _s()},
            ["event", "node", "outcome"],
            "Family core",
            {"event": "node_finished", "node": "agent", "outcome": "ok"},
        ),
        "DeltaFrame": frame(
            "delta",
            {"event": _s(), "text": _s(), "kind": _s()},
            ["event", "text"],
            "Family core — one assistant text delta",
            {"event": "delta", "text": "Your average"},
        ),
        "ToolCallFrame": frame(
            "tool_call",
            {
                "event": _s(),
                "id": _s(),
                "name": _s(),
                "status": _s(),
                "args": _s("Serialized-safe summary string, never raw payloads"),
                "result": _s(),
                "title": _s(),
                "duration_ms": {"type": "integer"},
            },
            ["event", "id", "name", "status"],
            "Family core",
            {
                "event": "tool_call",
                "id": "t1",
                "name": "search_observations",
                "status": "completed",
                "duration_ms": 412,
            },
        ),
        "FlowFinishedFrame": frame(
            "flow_finished",
            {
                "event": _s(),
                "run_id": _s(),
                "result_ref": _s(),
                "model": _s(),
                "total_ms": {"type": "integer"},
                "tool_count": {"type": "integer"},
            },
            ["event"],
            "Family core — terminal",
            {
                "event": "flow_finished",
                "run_id": "r-1",
                "model": "local",
                "total_ms": 2310,
                "tool_count": 1,
            },
        ),
        "FlowFailedFrame": frame(
            "flow_failed",
            {
                "event": _s(),
                "retryable": {"type": "boolean"},
                "code": _s("The stable error_type the client localizes — see sse.errorCodes"),
                "message": _s("Non-empty only for soft guard violations"),
            },
            ["event", "retryable"],
            "Family core — terminal guard",
            {
                "event": "flow_failed",
                "retryable": False,
                "code": "guard",
                "message": "message is too long (8000 char cap)",
            },
        ),
        "CitationFrame": frame(
            "citation",
            {"event": _s(), "source": _s("The data-source citation (tool name)")},
            ["event", "source"],
            "Health payload",
            {"event": "citation", "source": "search_observations"},
        ),
        "TaskFrame": frame(
            "task",
            {
                "event": _s(),
                "task": _open(
                    "Interactive HITL proposal card (ADR-0015); resolve via POST /ai/sessions/{id}/tasks/{proposal_id}/resolve"
                ),
            },
            ["event", "task"],
            "Health payload",
            {
                "event": "task",
                "task": {
                    "id": "t1a2b3c4-5d6e-4071-8293-b4c5d6e7f8a9",
                    "kind": "confirmation",
                    "title": "Log 500mg ferrous sulfate?",
                },
            },
        ),
        "InterruptFrame": frame(
            "interrupt",
            {"event": _s()},
            ["event"],
            "Health payload — the run paused at an ask_user interrupt (resumable via POST /ai/sessions/{id}/resume); deliberately NOT the family flow_interrupted broadcast stop",
            {"event": "interrupt"},
        ),
        "SessionFrame": frame(
            "session",
            {"event": _s(), "session_id": _s("The freshly auto-created chat session id")},
            ["event", "session_id"],
            "Health payload",
            {"event": "session", "session_id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9"},
        ),
    }


# ---------------------------------------------------------------------------
# SSE extraction
# ---------------------------------------------------------------------------


def _sse_section():
    from app.ai import flow_events_payload
    from app.ai.assistance import turns
    from app.ai.flow_events import FlowEvent as CoreFlowEvent

    names = [e.value for e in flow_events_payload.FlowEvent]
    provider_codes = [code for _name, code in turns._PROVIDER_ERROR_NAMES]
    return {
        "mediaType": "text/event-stream",
        "responseHeaders": turns.SSE_RESPONSE_HEADERS,
        "framing": "data: <single-line json>\\n\\n; ': keep-alive' comment frames during idle stretches",
        "keepAliveSeconds": turns.SSE_HEARTBEAT_SECONDS,
        "eventKey": "event",
        "eventNames": names,
        "eventNotes": {
            "flow_interrupted": "Deliberately ABSENT from health's served set (the resumable HITL pause is the `interrupt` payload event; the two interruption semantics are never conflated)",
        },
        "errorCodes": ["guard", "generic", *provider_codes],
        "errorFrameSemantics": "classify_stream_error: ValueError → code=guard with the user-facing message; provider errors → non-leaky codes with an empty message (the client localizes); retryable = connection|timeout|rate_limit",
        "coreEvents": [e.value for e in CoreFlowEvent],
    }


def _stream_frames():
    """Real frames from the real builders (extraction, not restatement)."""
    from app.ai import flow_events_payload as p
    from app.ai.flow_events import (
        delta_event,
        flow_failed_event,
        flow_finished_event,
        flow_started_event,
        node_finished_event,
        node_started_event,
        tool_call_event,
    )
    from app.ai.flow_events_payload import session_event

    def line(event: dict) -> str:
        return "data: " + json.dumps(event, separators=(",", ":"), default=str)

    frames = [
        flow_started_event("chat", run_id="r-1"),
        node_started_event("agent"),
        session_event("9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9"),
        p.citation_event("search_observations"),
        p.task_event(
            {
                "id": "t1a2b3c4-5d6e-4071-8293-b4c5d6e7f8a9",
                "kind": "confirmation",
                "title": "Log 500mg ferrous sulfate?",
            }
        ),
        delta_event("Your average sleep moved from 6.9h to 7.4h."),
        tool_call_event("t1", "search_observations", "completed", duration_ms=412),
        node_finished_event("agent", "ok"),
        flow_finished_event(run_id="r-1", model="local", total_ms=2310, tool_count=1),
    ]
    return [
        {
            "event": str(frame["event"]),
            "payload": {k: (str(v) if hasattr(v, "value") else v) for k, v in frame.items()},
            "line": line(frame),
        }
        for frame in frames
    ] + [
        {
            "event": "keep-alive",
            "payload": None,
            "line": ": keep-alive",
            "note": "Mandated-ignorable SSE comment emitted after SSE_HEARTBEAT_SECONDS of silence",
        },
        {
            "event": "flow_failed",
            "payload": {
                "event": "flow_failed",
                "retryable": False,
                "code": "guard",
                "message": "message is too long (8000 char cap)",
            },
            "line": line(
                flow_failed_event(
                    False, code="guard", message="message is too long (8000 char cap)"
                )
            ),
        },
        {
            "event": "flow_failed",
            "payload": {
                "event": "flow_failed",
                "retryable": True,
                "code": "rate_limit",
                "message": "",
            },
            "line": line(flow_failed_event(True, code="rate_limit", message="")),
        },
    ]


# ---------------------------------------------------------------------------
# Route assembly
# ---------------------------------------------------------------------------


def _envelope_response(item_component):
    return {"envelope": "ReadEnvelope", "responseItem": item_component}


def _build_routes(provider, ex, authored):
    cls = provider.HealthAssistantBridgeProvider
    bindings = _handler_model_bindings(cls)
    query_params = _handler_query_params(cls)

    def request_component(method, template, handler):
        """The request body shape: extracted from the handler's pydantic parse
        where one exists, authored otherwise (both coverage-gated)."""
        models = sorted(bindings.get(handler, ()))
        parse_models = [m for m in models if m not in ("ClientRecord", "ClientExaminationRecord")]
        if method in ("GET", "DELETE"):
            if parse_models:
                raise AssertionError(f"{handler} parses {parse_models} but is a {method}")
            return None
        if not parse_models:
            return None
        assert len(parse_models) == 1, f"{handler} parses multiple models: {parse_models}"
        name = parse_models[0]
        overrides = {
            "MapRequestPayload": "MapRequest",
            "SyncPayload": "SyncRequest",
            "MedicationRecordCreate": "MedicationCreateRequest",
            "AllergyIntoleranceCreate": "AllergyCreateRequest",
            "PatientImmunizationCreate": "VaccineCreateRequest",
            "ClinicalEventCreate": "ClinicalEventCreateRequest",
            "ClinicalEventUpdate": "ClinicalEventUpdateRequest",
            "ClinicalEventOccurrenceCreate": "ClinicalEventOccurrenceRequest",
            "MedicationRecordUpdate": "MedicationUpdateRequest",
            "AllergyIntoleranceUpdate": "AllergyUpdateRequest",
            "PatientImmunizationUpdate": "VaccineUpdateRequest",
        }
        return overrides.get(name, name)

    responses = _response_table(authored)
    handler_requests = _handler_request_overrides()
    empty_body = {
        ("POST", "documents/{doc_id}/extract"),
        ("POST", "notifications/read-all"),
        ("PATCH", "notifications/{recipient_id}/{action}"),
    }
    routes = []
    for method, template, raw, handler, params in _route_entries(provider._ROUTES):
        auth = "liveness-or-hmac" if (method == "GET" and template == "status") else "hmac"
        entry = {
            "method": method,
            "path": template,
            "pattern": raw,
            "handler": handler,
            "auth": auth,
        }
        if params:
            entry["pathParams"] = [
                {
                    "name": p,
                    "type": "string",
                    "description": "Segment captured by the dispatch regex",
                }
                for p in params
            ]
        qp = query_params.get(handler, [])
        if qp:
            entry["query"] = [{"name": n, "type": "string"} for n in qp]
        req = request_component(method, template, handler) or handler_requests.get(handler)
        if req is not None:
            entry["request"] = {"$ref": f"#/components/{req}"}
        elif method in ("POST", "PUT", "PATCH") and (method, template) not in empty_body:
            raise AssertionError(
                f"{method} {template} ({handler}) has no request shape — "
                "extend the generator tables (schema-first: a new body route without a shape fails here)"
            )
        resp = responses[(method, template)]
        entry.update(resp)
        routes.append(entry)
    routes.sort(key=lambda r: (r["method"], r["path"]))
    return routes


def _handler_request_overrides():
    """Authored request shapes for handlers that parse JSON manually (no
    pydantic model to extract). Coverage is enforced by route assembly."""
    return {
        "_ai_chat": "ChatTurnRequest",
        "_ai_chat_stream": "ChatTurnRequest",
        "_ai_resume_stream": "ResumeRequest",
        "_ai_rename_session": "SessionRenameRequest",
        "_ai_resolve_task": "ResolveTaskRequest",
        "_ai_transcribe": "TranscribeRequest",
        "_create_examination": "ExaminationCreateRequest",
        "_upload_document": "DocumentUploadRequest",
        "_create_doctor": "DoctorCreateRequest",
        "_update_doctor": "DoctorUpdateRequest",
        "_notification_set_preference": "PreferenceToggleRequest",
        "_notification_create_trigger": "TriggerCreateRequest",
        "_register_push_device": "DeviceRegistrationRequest",
    }


def _response_table(authored):
    """(method, template) → response descriptor. Authored, coverage-gated by
    route assembly (KeyError on a missing/unrouted entry fails --update)."""

    def comp(name):
        return {"component": name}

    def env(item, extra=None):
        out = _envelope_response(item)
        if extra:
            out["envelopeExtra"] = extra
        return out

    return {
        ("GET", "status"): {
            "oneOf": ["UnsignedStatus", "BridgeStatus"],
            "description": "Unsigned probe → UnsignedStatus; signed → the full BridgeStatus discovery payload",
        },
        ("POST", "map"): comp("MapResponse"),
        ("POST", "sync"): comp("SyncResponse"),
        ("GET", "observations/latest"): env("ObservationPoint"),
        ("GET", "observations"): env("ObservationPoint"),
        ("GET", "biomarkers"): env("BiomarkerSummary"),
        ("GET", "examinations"): env("ExaminationSummary"),
        ("POST", "examinations"): comp("ExaminationCreated"),
        ("POST", "examinations/{exam_id}/documents"): comp("DocumentUploadResult"),
        ("DELETE", "examinations/{exam_id}"): comp("DeleteResult"),
        ("GET", "examinations/{exam_id}"): comp("ExaminationDetail"),
        ("GET", "examinations/{exam_id}/documents"): env("DocumentSummary"),
        ("GET", "examinations/{exam_id}/status"): comp("ExamExtractionStatus"),
        ("GET", "examinations/{exam_id}/logs"): {"array": "ExtractionLogEntry"},
        ("GET", "documents"): env("DocumentSummary"),
        ("GET", "ai/sessions"): comp("SessionListResponse"),
        ("POST", "ai/chat"): comp("ChatTurnResult"),
        ("POST", "ai/chat/stream"): {
            "stream": True,
            "request": {"$ref": "#/components/ChatTurnRequest"},
        },
        ("POST", "ai/transcribe"): comp("TranscriptionResult"),
        ("GET", "ai/sessions/{session_id}/messages"): comp("MessagesResponse"),
        ("PATCH", "ai/sessions/{session_id}"): comp("SessionRenameResult"),
        ("DELETE", "ai/sessions/{session_id}"): comp("SuccessAck"),
        ("POST", "ai/sessions/{session_id}/tasks/{proposal_id}/resolve"): comp("ResolveTaskResult"),
        ("POST", "ai/sessions/{session_id}/resume"): {
            "stream": True,
            "request": {"$ref": "#/components/ResumeRequest"},
        },
        ("DELETE", "documents/{doc_id}"): comp("DeleteResult"),
        ("POST", "documents/{doc_id}/extract"): comp("ExtractionJob"),
        ("GET", "documents/{doc_id}"): comp("DocumentSummary"),
        ("GET", "documents/{doc_id}/content"): {
            "binary": True,
            "description": "The stored file bytes with the guessed content type",
        },
        ("GET", "documents/{doc_id}/preview"): {
            "binary": True,
            "description": "JPEG preview (?page=N for multi-page renders; X-Total-Pages/X-Current-Page headers)",
        },
        ("GET", "documents/{doc_id}/text"): comp("DocumentText"),
        ("GET", "documents/{doc_id}/extract/status"): comp("DocumentExtractionStatus"),
        ("GET", "medications"): env("Medication"),
        ("GET", "allergies"): env("Allergy"),
        ("GET", "vaccines"): env("Vaccine"),
        ("GET", "clinical-events"): env("ClinicalEventSummary"),
        ("GET", "clinical-events/{event_id}"): comp("ClinicalEventDetail"),
        ("GET", "doctors"): env("Doctor"),
        ("GET", "changes"): comp("ChangesResponse"),
        ("POST", "medications"): comp("Medication"),
        ("PUT", "medications/{medication_id}"): comp("Medication"),
        ("DELETE", "medications/{medication_id}"): comp("DeleteResult"),
        ("POST", "allergies"): comp("Allergy"),
        ("PUT", "allergies/{allergy_id}"): comp("Allergy"),
        ("DELETE", "allergies/{allergy_id}"): comp("DeleteResult"),
        ("POST", "vaccines"): comp("Vaccine"),
        ("PUT", "vaccines/{vaccine_id}"): comp("Vaccine"),
        ("DELETE", "vaccines/{vaccine_id}"): comp("DeleteResult"),
        ("POST", "clinical-events"): comp("ClinicalEventDetail"),
        ("PUT", "clinical-events/{event_id}"): comp("ClinicalEventDetail"),
        ("DELETE", "clinical-events/{event_id}"): comp("DeleteResult"),
        ("POST", "clinical-events/{event_id}/occurrences"): comp("ClinicalEventDetail"),
        ("POST", "doctors"): comp("Doctor"),
        ("PUT", "doctors/{doctor_id}"): comp("Doctor"),
        ("DELETE", "doctors/{doctor_id}"): comp("DeleteResult"),
        ("GET", "notifications/inbox"): comp("InboxResponse"),
        ("GET", "notifications/unread-count"): comp("UnreadCount"),
        ("POST", "notifications/read-all"): comp("ReadAllResult"),
        ("GET", "notifications/preferences"): env("NotificationKind"),
        ("PUT", "notifications/preferences/{kind_id}"): comp("PreferenceToggleResult"),
        ("GET", "notifications/triggers"): env("NotificationTrigger"),
        ("POST", "notifications/triggers"): comp("TriggerCreateResult"),
        ("DELETE", "notifications/triggers/{trigger_id}"): comp("DeleteResult"),
        ("PATCH", "notifications/{recipient_id}/{action}"): comp("StatusAck"),
        ("POST", "notifications/register-device"): comp("MobilePushTarget"),
        ("DELETE", "notifications/register-device/{device_id}"): comp("DeviceDeleteResult"),
        ("GET", "devices"): env("MobilePushTarget"),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _route_examples():
    """Per-route example bodies (the fixture wall's request side). Authored
    next to the response table; validated against the request component by
    the conformance tests."""
    return {
        ("POST", "map"): {"unmapped_metrics": [{"name": "Serum iron", "code": None}]},
        ("POST", "sync"): {
            "client_version": "2.1.0",
            "source_system": "myhealth",
            "cursor": "2026-09-30T08:15:00+00:00",
            "records": [
                {
                    "type": "quantitative",
                    "code": "2711-0",
                    "coding_system": "loinc",
                    "name": "Serum Iron",
                    "value": 62.0,
                    "unit": "ug/dL",
                    "timestamp": "2026-09-30T08:15:00+00:00",
                    "reference_range": {"low": 60.0, "high": 170.0},
                }
            ],
            "examinations": [
                {
                    "id": "report-8841",
                    "date": "2026-09-30T08:15:00+00:00",
                    "lab_name": "Meridian Labs",
                    "category": "Blood Test",
                    "diagnoses": ["Iron deficiency"],
                    "records": [
                        {
                            "type": "quantitative",
                            "code": "2711-0",
                            "coding_system": "loinc",
                            "name": "Serum Iron",
                            "value": 62.0,
                            "unit": "ug/dL",
                            "timestamp": "2026-09-30T08:15:00+00:00",
                        }
                    ],
                }
            ],
        },
        ("POST", "examinations"): {
            "id": "report-8841",
            "date": "2026-09-30T08:15:00+00:00",
            "lab_name": "Meridian Labs",
            "category": "Blood Test",
            "diagnoses": ["Iron deficiency"],
            "notes": "Fasting panel",
        },
        ("POST", "examinations/{exam_id}/documents"): {
            "id": "report-8841",
            "filename": "panel-2026-09-30.pdf",
            "data": "JVBERi0xLjQK",
            "content_type": "application/pdf",
            "include_in_extraction": True,
        },
        ("POST", "ai/chat"): {
            "message": "How did my sleep change this week?",
            "session_id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
        },
        ("POST", "ai/chat/stream"): {
            "message": "How did my sleep change this week?",
            "session_id": "9c1b2c3d-4e5f-4061-8273-84a5b6c7d8e9",
        },
        ("POST", "ai/transcribe"): {"data": "T2N0ZXRlU3RyZWFt", "mime": "audio/webm;codecs=opus"},
        ("PATCH", "ai/sessions/{session_id}"): {"title": "Sleep follow-up"},
        ("POST", "ai/sessions/{session_id}/tasks/{proposal_id}/resolve"): {
            "status": "confirmed",
            "result": {"dose": "500mg"},
        },
        ("POST", "ai/sessions/{session_id}/resume"): {
            "message_id": "b0a1b2c3-4d5e-4f60-8273-9a5b6c7d8e9f"
        },
        ("POST", "documents/{doc_id}/extract"): {},
        ("POST", "medications"): {
            "id": "med-21",
            "code": {"text": "Ferrous sulfate"},
            "status": "ACTIVE",
            "intent": "order",
            "dosage": "1 tablet daily",
            "frequency": {"repeat": {"per": 1, "unit": "d"}},
            "reason": "Iron deficiency",
            "start_date": "2026-10-01T00:00:00+00:00",
        },
        ("PUT", "medications/{medication_id}"): {"dosage": "2 tablets daily"},
        ("POST", "allergies"): {
            "id": "allergy-9",
            "code": {"text": "Penicillin"},
            "clinical_status": "ACTIVE",
            "category": "medication",
            "criticality": "low",
            "onset_date": "2019-03-02T00:00:00+00:00",
        },
        ("PUT", "allergies/{allergy_id}"): {"clinical_status": "RESOLVED"},
        ("POST", "vaccines"): {
            "id": "vax-3",
            "vaccine_code": {"text": "Influenza vaccine"},
            "status": "completed",
            "administered_at": "2025-10-05T00:00:00+00:00",
            "dose_number": "1",
            "manufacturer": "Generic Bio",
        },
        ("PUT", "vaccines/{vaccine_id}"): {"note": "Booster due 2026-10"},
        ("POST", "clinical-events"): {
            "id": "journey-11",
            "title": "Iron-deficiency workup",
            "status": "ACTIVE",
            "onset_date": "2026-09-30T00:00:00+00:00",
        },
        ("PUT", "clinical-events/{event_id}"): {
            "status": "RESOLVED",
            "resolved_date": "2026-10-10T00:00:00+00:00",
        },
        ("POST", "clinical-events/{event_id}/occurrences"): {
            "occurred_at": "2026-10-05T12:00:00+00:00",
            "title": "Follow-up draw",
            "severity": "mild",
        },
        ("POST", "doctors"): {
            "name": "Dr. Amara Osei",
            "specialty": "Hematology",
            "email": "a.osei@meridian.example",
        },
        ("PUT", "doctors/{doctor_id}"): {"office_number": "3C"},
        ("PUT", "notifications/preferences/{kind_id}"): {"enabled": False},
        ("POST", "notifications/triggers"): {
            "rule_type": "biomarker_threshold",
            "biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
            "operator": "lt",
            "value": 60.0,
            "severity": "warning",
            "cooldown_minutes": 720,
        },
        ("POST", "notifications/register-device"): {
            "device_id": "install-7f2a",
            "platform": "unifiedpush",
            "endpoint_url": "https://push.example/endpoint/abc",
            "app_version": "2.1.0",
            "user_agent": "HealthAssistant-Android/2.1.0",
        },
    }


def _error_fixtures():
    return [
        {
            "status": 404,
            "typed": True,
            "body": {
                "detail": {
                    "code": "not_found",
                    "message": "Examination not found for this patient.",
                }
            },
        },
        {
            "status": 409,
            "typed": True,
            "body": {
                "detail": {
                    "code": "turn_conflict",
                    "message": "A turn is already running for this session.",
                }
            },
        },
        {
            "status": 409,
            "typed": True,
            "body": {
                "detail": {"code": "hitl_conflict", "message": "This task was already resolved."}
            },
        },
        {
            "status": 400,
            "typed": True,
            "body": {"detail": {"code": "validation", "message": "message is required"}},
        },
        {
            "status": 502,
            "typed": True,
            "body": {"detail": {"code": "stt_failed", "message": "Upstream transcription failed."}},
        },
        {
            "status": 400,
            "typed": False,
            "body": {"detail": "No updatable fields supplied."},
            "note": "A bare ValueError reaches the proxy → 400 with a plain-string detail (typed:false; SDKs surface code: undefined)",
        },
        {
            "status": 401,
            "typed": False,
            "body": {"detail": "Invalid or missing X-Api-Signature."},
            "note": "Fail-closed auth (also: missing X-Api-Timestamp, or no api_secret configured)",
        },
        {
            "status": 400,
            "typed": False,
            "body": {"detail": "Path 'nope' with method 'GET' is not supported by the bridge API."},
            "note": "Unmatched route (NotImplementedError → 400)",
        },
    ]


def _build_fixtures(provider, components, routes):
    by_key = {(r["method"], r["path"]): r for r in routes}
    examples = _route_examples()
    out_routes = {}
    for (method, template), route in sorted(by_key.items()):
        entry = {"auth": route["auth"]}
        if "query" in route:
            entry["query"] = route["query"]
        req_example = examples.get((method, template))
        if req_example is not None:
            entry["requestExample"] = req_example
        if route.get("stream"):
            continue
        out_routes[f"{method} {template}"] = entry
    return {
        "meta": {
            "schemaVersion": SCHEMA_VERSION,
            "bridge": _bridge_identity(),
            "note": "The cross-SDK fixture wall (D23: implementations may differ, outputs may not) — one set, consumed by the python/ts/kotlin conformance suites. Generated from the schema; request/response examples are the component examples these entries reference.",
        },
        "routes": out_routes,
        "componentExamples": _component_examples(components, by_key),
        "errors": _error_fixtures(),
        "stream": {
            "route": "POST ai/chat/stream",
            "resumeRoute": "POST ai/sessions/{session_id}/resume",
            "frames": _stream_frames(),
        },
    }


def _component_examples(components, by_key):
    """Route → example payload pairs the SDK suites deserialize (the wall)."""
    wanted = [
        ("GET", "status", None, "BridgeStatus"),
        ("POST", "map", "MapRequest", "MapResponse"),
        ("POST", "sync", "SyncRequest", "SyncResponse"),
        ("POST", "ai/chat", "ChatTurnRequest", "ChatTurnResult"),
        ("PATCH", "ai/sessions/{session_id}", "SessionRenameRequest", "SessionRenameResult"),
        ("POST", "ai/transcribe", "TranscribeRequest", "TranscriptionResult"),
        (
            "POST",
            "ai/sessions/{session_id}/tasks/{proposal_id}/resolve",
            "ResolveTaskRequest",
            "ResolveTaskResult",
        ),
        ("GET", "ai/sessions/{session_id}/messages", None, "MessagesResponse"),
        ("GET", "ai/sessions", None, "SessionListResponse"),
        ("POST", "examinations", "ExaminationCreateRequest", "ExaminationCreated"),
        ("GET", "examinations", None, "ExaminationSummary"),
        ("GET", "examinations/{exam_id}", None, "ExaminationDetail"),
        ("GET", "observations", None, "ObservationPoint"),
        ("GET", "observations/latest", None, "ObservationPoint"),
        ("GET", "biomarkers", None, "BiomarkerSummary"),
        ("GET", "documents", None, "DocumentSummary"),
        ("GET", "documents/{doc_id}/text", None, "DocumentText"),
        ("GET", "medications", None, "Medication"),
        ("GET", "allergies", None, "Allergy"),
        ("GET", "vaccines", None, "Vaccine"),
        ("GET", "clinical-events", None, "ClinicalEventSummary"),
        ("GET", "doctors", None, "Doctor"),
        ("GET", "changes", None, "ChangesResponse"),
        ("GET", "notifications/inbox", None, "InboxResponse"),
        ("GET", "notifications/unread-count", None, "UnreadCount"),
        ("POST", "notifications/read-all", None, "ReadAllResult"),
        ("GET", "notifications/preferences", None, "NotificationKind"),
        (
            "PUT",
            "notifications/preferences/{kind_id}",
            "PreferenceToggleRequest",
            "PreferenceToggleResult",
        ),
        ("GET", "notifications/triggers", None, "NotificationTrigger"),
        ("POST", "notifications/triggers", "TriggerCreateRequest", "TriggerCreateResult"),
        ("PATCH", "notifications/{recipient_id}/{action}", None, "StatusAck"),
        ("POST", "notifications/register-device", "DeviceRegistrationRequest", "MobilePushTarget"),
        ("GET", "devices", None, "MobilePushTarget"),
        ("DELETE", "medications/{medication_id}", None, "DeleteResult"),
        ("POST", "medications", "MedicationCreateRequest", "Medication"),
        ("PUT", "medications/{medication_id}", "MedicationUpdateRequest", "Medication"),
        ("POST", "doctors", "DoctorCreateRequest", "Doctor"),
    ]
    out = []
    for method, template, req_comp, resp_comp in wanted:
        route = by_key.get((method, template))
        assert route is not None, f"fixture route missing: {method} {template}"
        assert (
            route.get("component") == resp_comp
            or route.get("responseItem") == resp_comp
            or resp_comp in (route.get("oneOf") or [])
        ), (
            f"{method} {template}: fixture component {resp_comp} != routed {route.get('component') or route.get('responseItem') or route.get('oneOf')}"
        )
        entry = {"route": f"{method} {template}", "responseComponent": resp_comp}
        if req_comp is not None:
            entry["requestComponent"] = req_comp
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _bridge_identity():
    manifest = json.loads((BRIDGE_DIR / "manifest.json").read_text())
    return {
        "id": manifest["id"],
        "version": manifest["version"],
        "kind": manifest["kind"],
        "apiVersion": manifest["apiVersion"],
        "kindVersion": manifest["kindVersion"],
    }


def build_schema():
    provider_mod = _load_provider()

    extracted = _extracted_components()
    components = _authored_components(extracted)

    # Mark the SDK-model-layer scope (the payloads the official client SDKs
    # traffic + typed errors + stream event types — D11's generated layers).
    frame_names = (
        "FlowStarted",
        "NodeStarted",
        "NodeFinished",
        "Delta",
        "ToolCall",
        "FlowFinished",
        "FlowFailed",
        "Citation",
        "Task",
        "Interrupt",
        "Session",
    )
    for name in (
        *(
            "BridgeStatus",
            "MetricMappingRequest",
            "MapRequest",
            "MappedMetric",
            "MapResponse",
            "ClientRecord",
            "ReferenceRange",
            "ClientExaminationRecord",
            "SyncRequest",
            "SyncResponse",
            "ChatTurnRequest",
            "ChatTurnResult",
            "ChatMessage",
            "SessionRenameRequest",
            "SessionRenameResult",
            "ResumeRequest",
            "ResolveTaskRequest",
            "ResolveTaskResult",
            "TranscribeRequest",
            "TranscriptionResult",
            "SuccessAck",
        ),
        *(f"{n}Frame" for n in frame_names),
    ):
        assert name in components, f"x-sdk component missing: {name}"
        components[name]["x-sdk"] = True

    routes = _build_routes(provider_mod, extracted, components)
    errors = _bridge_error_vocabulary(provider_mod.HealthAssistantBridgeProvider)

    # Stream frames carry their closed event name (from the builder-derived
    # example) as an enum — the discriminator the SDK model layers emit.
    for name, comp in components.items():
        if name.endswith("Frame") and comp is not components.get("StreamFrame"):
            example = (comp.get("examples") or [{}])[0]
            if "event" in comp.get("properties", {}) and example.get("event"):
                comp["properties"]["event"] = {
                    **comp["properties"]["event"],
                    "enum": [example["event"]],
                }

    # Examples the extractor cannot derive: the map response is an AI result
    # (authored, content-checked by the map route fixtures + suites) and the
    # exam detail composes the authored sub-shapes.
    components["MapResponse"]["examples"] = [
        {
            "mappings": [
                {
                    "original_name": "Serum iron",
                    "action": "map_to_existing",
                    "existing_biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
                },
                {
                    "original_name": "Ferritin panel",
                    "action": "create_new",
                    "new_biomarker_name": "Ferritin",
                    "new_biomarker_code": "2276-4",
                    "new_biomarker_coding_system": "loinc",
                },
            ]
        }
    ]
    components["ExaminationDetail"]["examples"] = [
        {
            **components["ExaminationSummary"]["examples"][0],
            "impressions": "Iron-deficiency pattern without acute findings.",
            "error_message": None,
            "external_id": "report-8841",
            "observations": [components["ExaminationObservation"]["examples"][0]],
            "doctors": [components["Doctor"]["examples"][0]],
            "medications": [components["Medication"]["examples"][0]],
            "clinical_events": [components["ExaminationClinicalEvent"]["examples"][0]],
        }
    ]

    # Extracted sync/map payload models carry authored examples (the extractor
    # cannot derive them; the contents are pinned by the sync/map fixtures).
    components["MetricMappingRequest"]["examples"] = [{"name": "Serum iron", "code": None}]
    components["MappedMetric"]["examples"] = [
        {
            "original_name": "Serum iron",
            "action": "map_to_existing",
            "existing_biomarker_id": "4d5e6f70-8192-43a4-b5c6-d7e8f9a0b1c2",
        }
    ]
    components["ClientRecord"]["examples"] = [
        {
            "type": "quantitative",
            "code": "2711-0",
            "coding_system": "loinc",
            "name": "Serum Iron",
            "value": 62.0,
            "unit": "ug/dL",
            "timestamp": "2026-09-30T08:15:00+00:00",
            "reference_range": {"low": 60.0, "high": 170.0},
        }
    ]
    components["ClientExaminationRecord"]["examples"] = [
        {
            "id": "report-8841",
            "date": "2026-09-30T08:15:00+00:00",
            "lab_name": "Meridian Labs",
            "category": "Blood Test",
            "diagnoses": ["Iron deficiency"],
            "records": [components["ClientRecord"]["examples"][0]],
        }
    ]
    components["MapRequest"]["examples"] = [
        {"unmapped_metrics": [components["MetricMappingRequest"]["examples"][0]]}
    ]
    components["SyncRequest"]["examples"] = [
        {
            "client_version": "2.1.0",
            "source_system": "myhealth",
            "cursor": "2026-09-30T08:15:00+00:00",
            "records": [components["ClientRecord"]["examples"][0]],
            "examinations": [components["ClientExaminationRecord"]["examples"][0]],
        }
    ]

    # Structural sanity: every internal ref must resolve.
    def _walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/"):
                assert ref.rsplit("/", 1)[1] in components, f"dangling ref: {ref}"
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(components)

    schema = {
        "schemaVersion": SCHEMA_VERSION,
        "id": "health-assistant-bridge",
        "title": "Health Assistant Bridge sub-API schema",
        "bridge": _bridge_identity(),
        "generatedFrom": "backend/app/extensions/builtin/health_assistant_bridge/provider.py::_ROUTES",
        "generator": "backend/scripts/export_bridge_schema.py",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "basePath": "/api/v1/integrations/health_assistant_bridge/api/{integration_id}",
        "publishRoute": "/api/v1/integrations/health_assistant_bridge/schema",
        "auth": {
            "scheme": "hmac-sha256",
            "canonical": "METHOD\\n<path>[?query]\\n<timestamp>\\n<raw_body>",
            "headers": {
                "X-Api-Signature": "hex HMAC-SHA256 digest",
                "X-Api-Timestamp": "epoch seconds (mandatory; ±replay window enforced server-side)",
            },
            "bodyCap": "server-enforced request body cap (audit M4)",
            "liveness": {
                "method": "GET",
                "path": "status",
                "unsigned": True,
                "response": "UnsignedStatus",
            },
            "authClasses": {
                "hmac": "signature + timestamp mandatory",
                "liveness-or-hmac": "GET status only: unsigned → minimal liveness answer; signed → full discovery payload",
            },
        },
        "errors": {
            "typedEnvelope": {
                "detail": {"code": _s("The closed code vocabulary below"), "message": _s()}
            },
            "codes": errors,
            "mappings": {
                "BridgeError": "HTTPException(status, detail={code, message})",
                "ValueError (bare)": "400 with a plain-string detail (typed:false)",
                "NotImplementedError": "400 unmatched-route detail",
                "auth failures": "401 plain-string details (fail-closed)",
            },
        },
        "handshake": {
            "unsignedStatus": "UnsignedStatus",
            "signedStatus": "BridgeStatus",
            "featureFlags": {
                "vocabulary": list(provider_mod.BRIDGE_FEATURES),
                "semantics": "Flags, not semver math, drive client gating; an older server omits bridge_features and clients degrade to legacy paths",
            },
            "latestSdks": {
                "shape": {"python": "semver", "ts": "semver", "kotlin": "semver"},
                "note": "Served from the bridge manifest's sdks block (empty object when the block is absent)",
            },
        },
        "sse": _sse_section(),
        "components": components,
        "routes": routes,
    }
    fixtures = _build_fixtures(provider_mod, components, routes)
    return schema, fixtures


def _render(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--update",
        action="store_true",
        help="rewrite the committed schema + fixtures (deliberate change)",
    )
    group.add_argument(
        "--check", action="store_true", help="fail when the live surface drifted (default)"
    )
    args = parser.parse_args()

    schema, fixtures = build_schema()
    rendered = {"schema": _render(schema), "fixtures": _render(fixtures)}

    if args.update or not SCHEMA_FILE.is_file():
        SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
        SCHEMA_FILE.write_text(rendered["schema"], encoding="utf-8")
        FIXTURES_FILE.write_text(rendered["fixtures"], encoding="utf-8")
        print(
            f"bridge schema written ({len(schema['routes'])} routes, {len(schema['components'])} components)"
        )
        return 0

    problems = []
    for path, key in ((SCHEMA_FILE, "schema"), (FIXTURES_FILE, "fixtures")):
        committed = path.read_text(encoding="utf-8")
        if committed != rendered[key]:
            diff = "\n".join(
                difflib.unified_diff(
                    committed.splitlines(),
                    rendered[key].splitlines(),
                    fromfile=str(path.relative_to(REPO_ROOT)) + " (committed)",
                    tofile="regenerated",
                    lineterm="",
                )
            )
            problems.append(diff)
    if problems:
        print("\n\n".join(problems), file=sys.stderr)
        print(
            "\nbridge schema DRIFT — re-run `uv run python scripts/export_bridge_schema.py --update` "
            "in the SAME commit as the provider/shape change",
            file=sys.stderr,
        )
        return 1
    print(
        f"bridge schema OK ({len(schema['routes'])} routes, {len(schema['components'])} components)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

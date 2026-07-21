"""The ``ask_user`` chatbot tool — LLM-initiated structured questions.

Sibling to the ``propose_*`` family. Where a proposal renders a *write-action*
review card, ``ask_user`` renders a *question card* so the LLM can gather
structured information from the user (free-text, single/multi-choice, or a
catalog/instance reference) before proceeding.

Reuses the existing HITL machinery unchanged: the ``__hitl__`` tool-result
marker, the ``[HITL_TASK]`` SSE sentinel, the ``tasks`` JSONB column on
``ChatMessage``, the ``/resolve`` endpoint, and the ``/resume`` continuation
turn. There is no DB migration, no new endpoint, no new sentinel.

Unlike ``propose_*``:

* **Read-only.** Resolving an ``ask_user`` task performs no REST write. The
  answers go to the LLM only (via the resolution feedback on resume). The
  "AI never writes" security model is preserved.
* **No inbox notification.** Questions are conversational, not work-to-clear.
* **Inline card.** The handler renders the form in the card body, not in a
  modal (faster intake).

See ``dev/plans/ai-ask-user-questions-2026-07-21.md`` for the full design.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.ai.tools.registry import ToolContext, register_chat_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hard caps — performance + token-budget guardrails
# ---------------------------------------------------------------------------

MAX_QUESTIONS_PER_BATCH = 8
MAX_OPTIONS_PER_QUESTION = 12
MAX_CANDIDATES_PER_REF = 6
MAX_PREFILTER_QUERY_LEN = 200  # for catalog_ref optional prefilter query
FREETEXT_ANSWER_TRIM_CHARS = 200  # used by the resume summary formatter


# ---------------------------------------------------------------------------
# Whitelists — closed sets, defense against LLM schema drift
# ---------------------------------------------------------------------------

#: Catalog types accepted by ``catalog_ref`` questions. Mirrors the catalogs
#: exposed by ``app.catalogs.registry`` and ``search_catalogs``.
ALLOWED_CATALOG_TYPES = frozenset(
    {
        "biomarker",
        "medication",
        "vaccine",
        "allergy",
        "anatomy",
        "concept",
        "clinical_event_type",
    }
)

#: Patient-scoped entity types accepted by ``instance_ref`` questions. Each
#: must have a snapshot function in :data:`INSTANCE_SNAPSHOT_BUILDERS`.
ALLOWED_INSTANCE_ENTITY_TYPES = frozenset({"clinical_event", "examination"})


# ---------------------------------------------------------------------------
# Pydantic question schema (discriminated union on ``kind``)
# ---------------------------------------------------------------------------


class ChoiceOption(BaseModel):
    """One selectable option for ``single_choice`` / ``multi_choice``."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(..., min_length=1, max_length=80, description="Stable id")
    label: str = Field(..., min_length=1, max_length=120, description="UI label")
    detail: Optional[str] = Field(
        None, max_length=200, description="Optional muted subtitle"
    )


class CandidateRef(BaseModel):
    """A pre-resolved catalog/instance row embedded server-side so the card
    can render without an extra round-trip. The user may still re-query.

    Rich fields (``code``, ``coding_system``, ``category``, ``is_telemetry``,
    ``unit``, ``date``, ``status``, ``kind``, ``description``) are populated
    opportunistically per entity type — they are surfaced to the LLM in the
    resolution feedback so the agent can act on the picked item WITHOUT a
    follow-up ``get_*_details`` call. The frontend mirrors this shape
    1:1 (``frontend/src/types/ai.ts``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(None, max_length=200)
    type: Optional[str] = Field(None, max_length=40)
    detail: Optional[str] = Field(None, max_length=240)

    # --- Coding / classification (catalog items) --------------------------
    code: Optional[str] = Field(None, max_length=80, description="Code (LOINC, SNOMED, etc.)")
    coding_system: Optional[str] = Field(None, max_length=40, description="loinc | snomed | custom | …")
    category: Optional[str] = Field(None, max_length=120, description="Biomarker category / exam category")
    kind: Optional[str] = Field(None, max_length=80, description="ConceptKind for concept entities")

    # --- Biomarker-specific -----------------------------------------------
    is_telemetry: Optional[bool] = Field(None, description="Biomarker: high-frequency IoT/wearable metric")
    unit: Optional[str] = Field(None, max_length=40, description="Biomarker preferred unit symbol")

    # --- Instance-specific (clinical_event, examination, …) ---------------
    date: Optional[str] = Field(None, max_length=40, description="ISO date for instance rows")
    status: Optional[str] = Field(None, max_length=40, description="active | resolved | final | …")

    # --- Free-form description (short) ------------------------------------
    description: Optional[str] = Field(None, max_length=300)


class _QuestionBase(BaseModel):
    """Common fields shared by every question kind."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ..., min_length=1, max_length=40, description="Stable id, unique within batch"
    )
    prompt: str = Field(..., min_length=1, max_length=500, description="User-facing prompt")
    help_text: Optional[str] = Field(None, max_length=300)
    required: bool = Field(False, description="Whether the user must answer")

    # `default` is overridden per-kind for tighter validation.


class FreetextQuestion(_QuestionBase):
    kind: str = Field("freetext", description="Discriminator — must be 'freetext'")
    placeholder: Optional[str] = Field(None, max_length=200)
    multiline: bool = Field(
        True, description="Render a textarea (true) or single-line input (false)"
    )
    default: Optional[str] = Field(None, max_length=2000)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "freetext":
            raise ValueError("kind must be 'freetext'")
        return v


class SingleChoiceQuestion(_QuestionBase):
    kind: str = Field("single_choice")
    options: List[ChoiceOption] = Field(..., min_length=1)
    default: Optional[str] = Field(None, max_length=80)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "single_choice":
            raise ValueError("kind must be 'single_choice'")
        return v

    @field_validator("default")
    @classmethod
    def _default_in_options(cls, v: Optional[str], info) -> Optional[str]:
        options = info.data.get("options") or []
        if v is not None and not any(opt.value == v for opt in options):
            raise ValueError(
                f"default value {v!r} is not one of the provided option values"
            )
        return v


class MultiChoiceQuestion(_QuestionBase):
    kind: str = Field("multi_choice")
    options: List[ChoiceOption] = Field(..., min_length=1)
    min_select: int = Field(0, ge=0)
    max_select: Optional[int] = Field(None, ge=0)
    default: Optional[List[str]] = Field(None)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "multi_choice":
            raise ValueError("kind must be 'multi_choice'")
        return v

    @field_validator("max_select")
    @classmethod
    def _max_ge_min(cls, v: Optional[int], info) -> Optional[int]:
        min_sel = info.data.get("min_select", 0) or 0
        if v is not None and v < min_sel:
            raise ValueError("max_select cannot be less than min_select")
        return v

    @field_validator("default")
    @classmethod
    def _defaults_in_options(cls, v: Optional[List[str]], info) -> Optional[List[str]]:
        options = info.data.get("options") or []
        valid = {opt.value for opt in options}
        if v is not None:
            bad = [x for x in v if x not in valid]
            if bad:
                raise ValueError(
                    f"default values {bad!r} are not among the provided options"
                )
        return v


class CatalogRefQuestion(_QuestionBase):
    kind: str = Field("catalog_ref")
    catalog_type: str = Field(..., description="One of: " + ", ".join(sorted(ALLOWED_CATALOG_TYPES)))
    multi: bool = False
    #: Opaque server-side filter; currently supports `query` (str) and
    #: `is_telemetry` (bool, biomarker only). Unknown keys are ignored.
    prefilter: Optional[Dict[str, Any]] = Field(None)
    #: Server-snapshot, populated by the tool. The LLM should NOT pass this.
    candidates: Optional[List[CandidateRef]] = Field(None)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "catalog_ref":
            raise ValueError("kind must be 'catalog_ref'")
        return v

    @field_validator("catalog_type")
    @classmethod
    def _check_catalog_type(cls, v: str) -> str:
        if v not in ALLOWED_CATALOG_TYPES:
            raise ValueError(
                f"catalog_type {v!r} is not supported. Allowed: "
                + ", ".join(sorted(ALLOWED_CATALOG_TYPES))
            )
        return v

    @field_validator("prefilter")
    @classmethod
    def _check_prefilter(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("prefilter must be an object")
        # Whitelist keys; ignore unknown ones rather than reject — the LLM
        # may add future-use fields that don't break anything.
        allowed = {"query", "is_telemetry", "kind"}
        unknown = set(v.keys()) - allowed
        if unknown:
            raise ValueError(
                f"prefilter keys {sorted(unknown)!r} not recognised; "
                f"allowed: {sorted(allowed)}"
            )
        if "query" in v and isinstance(v["query"], str):
            if len(v["query"]) > MAX_PREFILTER_QUERY_LEN:
                raise ValueError("prefilter.query is too long")
        return v


class InstanceRefQuestion(_QuestionBase):
    kind: str = Field("instance_ref")
    entity_type: str = Field(..., description="One of: " + ", ".join(sorted(ALLOWED_INSTANCE_ENTITY_TYPES)))
    patient_scope: bool = Field(True, description="Must be true for now (patient-scoped only)")
    multi: bool = False
    candidates: Optional[List[CandidateRef]] = Field(None)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v != "instance_ref":
            raise ValueError("kind must be 'instance_ref'")
        return v

    @field_validator("entity_type")
    @classmethod
    def _check_entity_type(cls, v: str) -> str:
        if v not in ALLOWED_INSTANCE_ENTITY_TYPES:
            raise ValueError(
                f"entity_type {v!r} is not supported. Allowed: "
                + ", ".join(sorted(ALLOWED_INSTANCE_ENTITY_TYPES))
            )
        return v

    @field_validator("patient_scope")
    @classmethod
    def _must_be_patient_scope(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("patient_scope must be true (cross-patient is not supported)")
        return v


#: Discriminated union of all question kinds.
Question = Union[
    FreetextQuestion,
    SingleChoiceQuestion,
    MultiChoiceQuestion,
    CatalogRefQuestion,
    InstanceRefQuestion,
]

_QUESTION_ADAPTER: TypeAdapter = TypeAdapter(List[Question])


# ---------------------------------------------------------------------------
# Validation + normalization
# ---------------------------------------------------------------------------


class AskUserValidationError(ValueError):
    """Raised when the LLM-supplied question batch is invalid. The message is
    safe to return to the LLM as a tool result so it can self-correct."""


def _validate_batch(questions_raw: Any) -> List[BaseModel]:
    """Validate the LLM-supplied ``questions`` argument.

    Returns the list of typed question models. Raises
    :class:`AskUserValidationError` with a clear, LLM-facing message.
    """
    if not isinstance(questions_raw, list):
        raise AskUserValidationError(
            "`questions` must be a list of question objects."
        )
    if not questions_raw:
        raise AskUserValidationError("`questions` must contain at least one question.")
    if len(questions_raw) > MAX_QUESTIONS_PER_BATCH:
        raise AskUserValidationError(
            f"Too many questions: {len(questions_raw)} > {MAX_QUESTIONS_PER_BATCH}. "
            "Split into multiple turns or pick the most important ones."
        )

    # Pydantic discriminated-union validation. Errors carry the per-question
    # index in their loc prefix, which makes self-correction tractable.
    try:
        typed = _QUESTION_ADAPTER.validate_python(questions_raw)
    except Exception as exc:  # pydantic.ValidationError
        raise AskUserValidationError(_format_pydantic_errors(exc)) from None

    # Per-kind caps that pydantic can't express cleanly.
    seen_ids: set[str] = set()
    for i, q in enumerate(typed):
        if q.id in seen_ids:
            raise AskUserValidationError(
                f"Question #{i + 1} reuses id {q.id!r}; ids must be unique within a batch."
            )
        seen_ids.add(q.id)

        options = getattr(q, "options", None) or []
        if options and len(options) > MAX_OPTIONS_PER_QUESTION:
            raise AskUserValidationError(
                f"Question {q.id!r} has {len(options)} options; the cap is "
                f"{MAX_OPTIONS_PER_QUESTION}."
            )

    return typed


def _format_pydantic_errors(exc: Exception) -> str:
    """Compact, LLM-facing error string from a pydantic ValidationError."""
    errs = getattr(exc, "errors", None)
    if not callable(errs):
        return f"Question payload failed validation: {exc}"
    parts = []
    for e in errs()[:10]:  # cap to keep the tool result readable
        loc = ".".join(str(x) for x in e.get("loc", []) if str(x) != "root")
        msg = e.get("msg", "invalid")
        ctx = e.get("ctx") or {}
        ctx_str = f" ({ctx})" if ctx else ""
        parts.append(f"{loc or 'root'}: {msg}{ctx_str}".strip())
    return "Invalid question payload: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Candidate snapshot (catalog_ref / instance_ref)
# ---------------------------------------------------------------------------


async def _snapshot_catalog_candidates(
    ctx: ToolContext, question: CatalogRefQuestion
) -> List[CandidateRef]:
    """Run cross-catalog search for a ``catalog_ref`` question and snapshot
    the top-N hits as :class:`CandidateRef`. Returns ``[]`` on miss/error —
    the frontend still lets the user re-query.

    The snapshot is RICH: ``search_catalogs(enrich=True)`` returns each hit
    via the catalog adapter's ``serialize()``, so we capture not just
    identification (id/name/slug/type) but also coding (``code``,
    ``coding_system``), classification (``category``, ``kind``), and any
    type-specific fields the adapter exposes (``is_telemetry``,
    ``preferred_unit_symbol`` for biomarkers, indications for medications,
    etc.). These surface to the LLM in the resolution feedback so the agent
    can act on the user's pick WITHOUT a follow-up ``get_*_details`` call.
    """
    prefilter = question.prefilter or {}
    query = prefilter.get("query") or ""
    if not query:
        # Without a seed query, there's nothing useful to pre-search. The
        # frontend EntityPicker will run the first query when the user types.
        return []
    try:
        from app.services.catalog_search_service import search_catalogs

        kind = None
        if "kind" in prefilter and question.catalog_type == "concept":
            try:
                from app.models.enums import ConceptKind

                kind = ConceptKind(prefilter["kind"])
            except Exception:
                kind = None

        hits = await search_catalogs(
            ctx.db,
            ctx.tenant_id,
            query,
            types=[question.catalog_type],
            limit_total=MAX_CANDIDATES_PER_REF,
            enrich=True,
        )
    except Exception:
        logger.exception(
            "ask_user: catalog candidate snapshot failed for type=%s query=%r",
            question.catalog_type,
            query,
        )
        return []

    out: List[CandidateRef] = []
    for hit in hits:
        out.append(_hit_to_candidate(hit, question.catalog_type))
    return out


def _hit_to_candidate(hit: Dict[str, Any], fallback_type: str) -> CandidateRef:
    """Build a :class:`CandidateRef` from a ``search_catalogs`` enriched hit.

    Captures all rich fields the adapter exposes — coding, classification,
    type-specific flags — so the LLM can act on the picked item without a
    re-fetch. Unknown/missing fields default to ``None`` and are stripped at
    serialization time (``model_dump(exclude_none=True)``).
    """
    name = str(hit.get("label") or hit.get("name") or hit.get("id"))
    description = hit.get("description") or hit.get("snippet")

    # is_telemetry may arrive as a bool or as a string ("true"/"false") from
    # some adapters — normalise to bool or None.
    raw_telemetry = hit.get("is_telemetry")
    is_telemetry: Optional[bool]
    if isinstance(raw_telemetry, bool):
        is_telemetry = raw_telemetry
    elif isinstance(raw_telemetry, str):
        is_telemetry = raw_telemetry.lower() in {"true", "1", "yes"}
    else:
        is_telemetry = None

    return CandidateRef(
        id=str(hit.get("id")),
        name=name,
        slug=hit.get("slug"),
        type=hit.get("type") or fallback_type,
        detail=_truncate(description, 240),
        code=hit.get("code"),
        coding_system=hit.get("coding_system"),
        category=hit.get("category"),
        kind=hit.get("kind"),
        is_telemetry=is_telemetry,
        unit=hit.get("preferred_unit_symbol") or hit.get("unit_symbol"),
        description=_truncate(description, 300),
    )


def _truncate(s: Optional[str], cap: int) -> Optional[str]:
    """Truncate ``s`` to AT MOST ``cap`` characters (ellipsis included when
    truncation occurs). Returns ``None`` for empty/None input."""
    if not s:
        return None
    s = str(s)
    if len(s) <= cap:
        return s
    # Reserve one char for the ellipsis.
    return s[: max(0, cap - 1)].rstrip() + "…"


async def _snapshot_clinical_events(ctx: ToolContext) -> List[CandidateRef]:
    """Top open patient clinical events, newest first. Carries title, status,
    onset_date so the LLM can disambiguate without a re-fetch."""
    try:
        from sqlalchemy import select

        from app.models.clinical_event import ClinicalEvent

        res = await ctx.db.execute(
            select(ClinicalEvent)
            .where(
                ClinicalEvent.patient_id == ctx.patient_id,
                ClinicalEvent.tenant_id == ctx.tenant_id,
                ClinicalEvent.deleted_at.is_(None),
            )
            .order_by(ClinicalEvent.created_at.desc())
            .limit(MAX_CANDIDATES_PER_REF)
        )
        events = res.scalars().all()
        out: List[CandidateRef] = []
        for e in events:
            out.append(
                CandidateRef(
                    id=str(e.id),
                    name=getattr(e, "title", None) or "Untitled",
                    type="clinical_event",
                    status=getattr(e, "status", None),
                    date=(
                        e.onset_date.isoformat()
                        if getattr(e, "onset_date", None)
                        else None
                    ),
                )
            )
        return out
    except Exception:
        logger.exception("ask_user: clinical_event snapshot failed")
        return []


async def _snapshot_examinations(ctx: ToolContext) -> List[CandidateRef]:
    """Top patient examinations, newest first. Carries date + category so the
    LLM can disambiguate without a re-fetch."""
    try:
        from sqlalchemy import select

        from app.models.examination_model import ExaminationModel

        res = await ctx.db.execute(
            select(ExaminationModel)
            .where(
                ExaminationModel.patient_id == ctx.patient_id,
                ExaminationModel.tenant_id == ctx.tenant_id,
                ExaminationModel.deleted_at.is_(None),
            )
            .order_by(ExaminationModel.examination_date.desc().nullslast())
            .limit(MAX_CANDIDATES_PER_REF)
        )
        exams = res.scalars().all()
        out: List[CandidateRef] = []
        for e in exams:
            date_str = (
                e.examination_date.isoformat() if e.examination_date else None
            )
            out.append(
                CandidateRef(
                    id=str(e.id),
                    name=date_str or "Unknown date",
                    type="examination",
                    date=date_str,
                    status=getattr(e, "extraction_status", None),
                )
            )
        return out
    except Exception:
        logger.exception("ask_user: examination snapshot failed")
        return []


#: Maps ``entity_type`` → snapshot builder for ``instance_ref`` questions.
INSTANCE_SNAPSHOT_BUILDERS = {
    "clinical_event": _snapshot_clinical_events,
    "examination": _snapshot_examinations,
}


async def _populate_candidates(ctx: ToolContext, question: BaseModel) -> BaseModel:
    """Attach a server-side candidate snapshot to ``catalog_ref`` / ``instance_ref``
    questions. Other kinds pass through unchanged. Best-effort — never raises."""
    try:
        if isinstance(question, CatalogRefQuestion):
            if question.candidates is None:
                question.candidates = await _snapshot_catalog_candidates(ctx, question)
        elif isinstance(question, InstanceRefQuestion):
            if question.candidates is None:
                builder = INSTANCE_SNAPSHOT_BUILDERS.get(question.entity_type)
                if builder is not None:
                    question.candidates = await builder(ctx)
                else:
                    question.candidates = []
    except Exception:
        logger.exception(
            "ask_user: candidate population failed for question %r", question.id
        )
    return question


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


@register_chat_tool("ask_user")
def build(ctx: ToolContext) -> List[Any]:
    """Build the ``ask_user`` tool, closure-bound to ``ctx``."""

    @tool
    async def ask_user(
        questions: List[dict],
        title: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> str:
        """Ask the user one or more structured clarifying questions.

        Renders an INLINE question card the user fills in and submits. The
        answers come back to you on the next turn (via the resolution
        feedback) so you can proceed. Use this when you genuinely cannot
        proceed without input that you cannot reasonably guess — e.g.
        picking the right biomarker to link, asking which of several missing
        items to create, or clarifying a free-text detail.

        This is NOT for write actions. Use ``propose_*`` tools to render
        review cards for creates. ``ask_user`` is read-only: it produces no
        side-effect; the answers go to you only.

        RULES:
        * At most ONE ``ask_user`` call per turn.
        * Batch related questions into one call (up to 8 questions). Do NOT
          emit multiple ``ask_user`` calls in the same turn.
        * Do NOT ask a question whose answer you can derive from tools
          (e.g. do not ask "what is the patient's latest glucose?" — call
          ``get_biomarker_history``).
        * Prefer ``catalog_ref`` / ``instance_ref`` over ``freetext`` when
          the answer must be an existing entity; it gives the user a picker
          and avoids typos.
        * ``id`` must be stable — the answers come back keyed by it.

        Question shape (one per list item):
        ```
        {
          "id": "q_dose",                       // stable, unique in batch
          "kind": "freetext",                   // see kinds below
          "prompt": "What dosage?",
          "help_text": "Optional, muted",
          "required": true,                     // default false

          // freetext only:
          "placeholder": "e.g. 500 mg",
          "multiline": true,                    // default true
          "default": "500 mg",

          // single_choice / multi_choice only:
          "options": [{"value": "oral", "label": "Oral", "detail": "by mouth"}],
          // multi_choice only:
          "min_select": 0, "max_select": null,
          "default": ["oral"],                  // multi_choice only

          // catalog_ref only:
          "catalog_type": "biomarker",          // biomarker|medication|vaccine|allergy|anatomy|concept|clinical_event_type
          "multi": false,
          "prefilter": {"query": "HbA1c", "is_telemetry": false},
          //   ^ prefilter.query triggers a server-side candidate snapshot
          //     so the card renders with the top matches pre-populated.

          // instance_ref only:
          "entity_type": "clinical_event",      // clinical_event|examination
          "patient_scope": true,                // must be true
          "multi": false
        }
        ```

        Kinds:
        * ``freetext`` — text answer.
        * ``single_choice`` — pick one from options (radio list).
        * ``multi_choice`` — pick N from options (checkbox list).
        * ``catalog_ref`` — pick an existing catalog item via a search picker.
        * ``instance_ref`` — pick an existing patient-scoped entity (e.g. a
          health journey or examination) the user wants to attach to.

        Args:
            questions: List of 1–8 question objects (see shape above).
            title: Optional card title. Defaults to "Quick questions".
            summary: Optional one-sentence rationale shown above the questions.

        Returns:
            A JSON string with ``{"__hitl__": true, "task": {...}}``. The
            chat loop renders the question card and waits for the user. If
            the payload is invalid, returns ``{"error": "..."}`` instead so
            you can self-correct.
        """
        # 1. Validate (raises AskUserValidationError on bad payload).
        try:
            typed = _validate_batch(questions)
        except AskUserValidationError as e:
            return json.dumps({"error": str(e)})

        # 2. Populate candidate snapshots for ref-kind questions.
        for i, q in enumerate(typed):
            typed[i] = await _populate_candidates(ctx, q)

        # 3. Build the HITL task payload.
        proposed_payload = {
            "summary": (summary or "").strip()[:300] or None,
            "questions": [_question_to_payload(q) for q in typed],
        }
        task = {
            "schema_version": 2,
            "proposal_id": str(uuid4()),
            "task_type": "ask_user",
            "title": (title or "Quick questions").strip()[:120],
            "status": "proposed",
            "proposed_payload": proposed_payload,
            "context": {
                "patient_id": str(ctx.patient_id) if ctx.patient_id else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved": None,
        }
        return json.dumps({"__hitl__": True, "task": task})

    return [ask_user]


# ---------------------------------------------------------------------------
# Payload serialization
# ---------------------------------------------------------------------------


def _question_to_payload(q: BaseModel) -> Dict[str, Any]:
    """Serialize a typed question model to the JSONB-friendly dict stored
    in ``proposed_payload.questions``. Strips ``None`` values for compactness."""
    data = q.model_dump(exclude_none=True, mode="json")
    # Normalize candidate_ref embedded objects (pydantic dumps them as dicts
    # already; ensure key order is stable for test snapshots).
    if "candidates" in data and data["candidates"] is not None:
        data["candidates"] = [
            {k: v for k, v in (c or {}).items() if v is not None}
            for c in data["candidates"]
        ]
    return data


__all__ = [
    "ALLOWED_CATALOG_TYPES",
    "ALLOWED_INSTANCE_ENTITY_TYPES",
    "MAX_QUESTIONS_PER_BATCH",
    "MAX_OPTIONS_PER_QUESTION",
    "MAX_CANDIDATES_PER_REF",
    "FREETEXT_ANSWER_TRIM_CHARS",
    "AskUserValidationError",
    "build",
    "_validate_batch",
    "_format_pydantic_errors",
    "_question_to_payload",
]

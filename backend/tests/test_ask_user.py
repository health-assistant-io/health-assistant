"""Tests for the ``ask_user`` chatbot tool — LLM-initiated structured questions.

Pins four contracts:

1. **Pydantic schema validation** — the discriminated union accepts the five
   declared question kinds and rejects malformed payloads (unknown kind,
   unknown catalog_type, duplicate ids, default not in options, …).
2. **Hard caps** — the tool refuses batches/questions/options that exceed the
   guardrails (8 questions, 12 options, etc.) so the LLM cannot bloat the
   resume prompt or the SSE payload.
3. **Candidate snapshot** — ``catalog_ref`` / ``instance_ref`` questions get a
   best-effort server-side snapshot; failures degrade gracefully (empty list,
   never raises).
4. **Tool end-to-end** — the ``ask_user`` factory produces a working LangChain
   tool whose invocation returns the ``{"__hitl__": True, "task": ...}``
   payload expected by ``_parse_hitl_proposal``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.ai.agents.hitl import _hitl_resolution_summary, _parse_hitl_proposal
from app.ai.tools import ask_user as au
from app.ai.tools.ask_user import (
    ALLOWED_CATALOG_TYPES,
    ALLOWED_INSTANCE_ENTITY_TYPES,
    MAX_CANDIDATES_PER_REF,
    MAX_OPTIONS_PER_QUESTION,
    MAX_QUESTIONS_PER_BATCH,
    AskUserValidationError,
    _question_to_payload,
    _validate_batch,
    build,
)
from app.ai.tools.registry import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(db=None) -> ToolContext:
    return ToolContext(
        db=db or MagicMock(),
        tenant_id=uuid4(),
        patient_id=uuid4(),
    )


def _q(**overrides):
    """Build a minimal valid freetext question."""
    base = {"id": "q1", "kind": "freetext", "prompt": "How much?"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Pydantic schema validation
# ---------------------------------------------------------------------------


def test_validate_freetext_question():
    typed = _validate_batch([_q(placeholder="500 mg", required=True)])
    assert len(typed) == 1
    assert typed[0].kind == "freetext"
    assert typed[0].placeholder == "500 mg"
    assert typed[0].required is True


def test_validate_single_choice_default_must_be_in_options():
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "single_choice",
                    "prompt": "Pick",
                    "options": [{"value": "a", "label": "A"}],
                    "default": "b",
                }
            ]
        )
    assert "default" in str(exc.value).lower()


def test_validate_single_choice_valid_default():
    typed = _validate_batch(
        [
            {
                "id": "q1",
                "kind": "single_choice",
                "prompt": "Pick",
                "options": [
                    {"value": "a", "label": "A"},
                    {"value": "b", "label": "B"},
                ],
                "default": "b",
            }
        ]
    )
    assert typed[0].default == "b"


def test_validate_multi_choice_defaults_must_be_in_options():
    with pytest.raises(AskUserValidationError):
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "multi_choice",
                    "prompt": "Pick",
                    "options": [{"value": "a", "label": "A"}],
                    "default": ["a", "z"],
                }
            ]
        )


def test_validate_multi_choice_max_ge_min():
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "multi_choice",
                    "prompt": "Pick",
                    "options": [{"value": "a", "label": "A"}],
                    "min_select": 3,
                    "max_select": 1,
                }
            ]
        )
    assert "max_select" in str(exc.value).lower()


def test_validate_catalog_ref_rejects_unknown_type():
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "catalog_ref",
                    "prompt": "Pick",
                    "catalog_type": "not_a_real_catalog",
                }
            ]
        )
    assert "catalog_type" in str(exc.value).lower()


def test_validate_catalog_ref_accepts_every_advertised_type():
    for t in ALLOWED_CATALOG_TYPES:
        typed = _validate_batch(
            [{"id": "q1", "kind": "catalog_ref", "prompt": "Pick", "catalog_type": t}]
        )
        assert typed[0].catalog_type == t


def test_validate_instance_ref_rejects_unknown_entity_type():
    with pytest.raises(AskUserValidationError):
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "instance_ref",
                    "prompt": "Pick",
                    "entity_type": "insurance_claim",
                }
            ]
        )


def test_validate_instance_ref_must_be_patient_scope():
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "instance_ref",
                    "prompt": "Pick",
                    "entity_type": "clinical_event",
                    "patient_scope": False,
                }
            ]
        )
    assert "patient_scope" in str(exc.value).lower()


def test_validate_rejects_unknown_kind():
    with pytest.raises(AskUserValidationError):
        _validate_batch([{"id": "q1", "kind": "rating", "prompt": "Stars"}])


def test_validate_rejects_extra_fields():
    """Unknown fields must be rejected (extra='forbid') so the LLM cannot
    smuggle in fields the frontend does not know how to render."""
    with pytest.raises(AskUserValidationError):
        _validate_batch([{"id": "q1", "kind": "freetext", "prompt": "Hi", "banana": 1}])


def test_validate_rejects_duplicate_ids():
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [
                {"id": "dup", "kind": "freetext", "prompt": "A"},
                {"id": "dup", "kind": "freetext", "prompt": "B"},
            ]
        )
    assert "unique" in str(exc.value).lower()


def test_validate_rejects_non_list():
    with pytest.raises(AskUserValidationError):
        _validate_batch({"id": "q1", "kind": "freetext", "prompt": "Hi"})


def test_validate_rejects_empty_list():
    with pytest.raises(AskUserValidationError):
        _validate_batch([])


def test_validate_rejects_prefilter_unknown_keys():
    with pytest.raises(AskUserValidationError):
        _validate_batch(
            [
                {
                    "id": "q1",
                    "kind": "catalog_ref",
                    "prompt": "Pick",
                    "catalog_type": "biomarker",
                    "prefilter": {"query": "hba1c", "color": "blue"},
                }
            ]
        )


# ---------------------------------------------------------------------------
# 2. Hard caps
# ---------------------------------------------------------------------------


def test_cap_max_questions():
    too_many = [
        {"id": f"q{i}", "kind": "freetext", "prompt": f"P{i}"} for i in range(MAX_QUESTIONS_PER_BATCH + 1)
    ]
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(too_many)
    assert "too many" in str(exc.value).lower()


def test_cap_max_options_per_question():
    options = [
        {"value": str(i), "label": f"Opt{i}"} for i in range(MAX_OPTIONS_PER_QUESTION + 1)
    ]
    with pytest.raises(AskUserValidationError) as exc:
        _validate_batch(
            [{"id": "q1", "kind": "single_choice", "prompt": "Pick", "options": options}]
        )
    assert "cap" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 3. Candidate snapshot (best-effort, never raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_captures_rich_fields():
    """The snapshot must carry code/coding_system/category/is_telemetry/unit
    so the LLM can act on the user's pick WITHOUT a follow-up fetch."""
    fake_hits = [
        {
            "id": "u1",
            "label": "HbA1c",
            "slug": "hba1c",
            "type": "biomarker",
            "description": "Glycated hemoglobin",
            "code": "4548-4",
            "coding_system": "loinc",
            "category": "Hematology",
            "is_telemetry": False,
            "preferred_unit_symbol": "%",
        }
    ]
    ctx = _ctx()
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=fake_hits),
    ):
        from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

        q = CatalogRefQuestion(
            id="q1",
            kind="catalog_ref",
            prompt="Pick",
            catalog_type="biomarker",
            prefilter={"query": "hba1c"},
        )
        candidates = await _snapshot_catalog_candidates(ctx, q)
    assert len(candidates) == 1
    c = candidates[0]
    # Identity fields
    assert c.id == "u1"
    assert c.name == "HbA1c"
    assert c.slug == "hba1c"
    assert c.type == "biomarker"
    # Coding / classification
    assert c.code == "4548-4"
    assert c.coding_system == "loinc"
    assert c.category == "Hematology"
    # Biomarker-specific
    assert c.is_telemetry is False
    assert c.unit == "%"
    # Description
    assert c.description == "Glycated hemoglobin"


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_normalises_string_telemetry_flag():
    """Some adapters serialise is_telemetry as 'true'/'false' strings.
    The snapshot normalises to a real bool so the LLM (and JSON output)
    see a consistent type."""
    fake_hits = [
        {
            "id": "u1",
            "label": "Heart Rate",
            "type": "biomarker",
            "is_telemetry": "true",
            "preferred_unit_symbol": "bpm",
        }
    ]
    ctx = _ctx()
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=fake_hits),
    ):
        from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

        q = CatalogRefQuestion(
            id="q1",
            kind="catalog_ref",
            prompt="Pick",
            catalog_type="biomarker",
            prefilter={"query": "hr"},
        )
        candidates = await _snapshot_catalog_candidates(ctx, q)
    assert candidates[0].is_telemetry is True


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_truncates_long_description():
    """The description cap (300 chars) is enforced so a single candidate
    cannot blow the SSE payload or the resume prompt."""
    long_desc = "x" * 1000
    fake_hits = [{"id": "u1", "label": "X", "type": "medication", "description": long_desc}]
    ctx = _ctx()
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=fake_hits),
    ):
        from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

        q = CatalogRefQuestion(
            id="q1", kind="catalog_ref", prompt="Pick", catalog_type="medication",
            prefilter={"query": "x"},
        )
        candidates = await _snapshot_catalog_candidates(ctx, q)
    assert candidates[0].description is not None
    assert candidates[0].description.endswith("…")
    assert len(candidates[0].description or "") <= 301  # 300 + ellipsis


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_caps_at_max():
    """Even if search returns more, the snapshot caps at MAX_CANDIDATES_PER_REF."""
    fake_hits = [
        {"id": f"u{i}", "label": f"H{i}"} for i in range(50)
    ]
    ctx = _ctx()
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=fake_hits[:MAX_CANDIDATES_PER_REF]),
    ) as mock_search:
        from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

        q = CatalogRefQuestion(
            id="q1",
            kind="catalog_ref",
            prompt="Pick",
            catalog_type="biomarker",
            prefilter={"query": "h"},
        )
        candidates = await _snapshot_catalog_candidates(ctx, q)
    # The service itself is expected to honour limit_total; we only verify the
    # adapter never returns more than the cap.
    assert len(candidates) <= MAX_CANDIDATES_PER_REF
    assert mock_search.await_count == 1
    assert mock_search.await_args.kwargs["limit_total"] == MAX_CANDIDATES_PER_REF


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_without_query_returns_empty():
    """No `prefilter.query` → no snapshot (frontend will run the first query
    when the user types)."""
    ctx = _ctx()
    from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

    q = CatalogRefQuestion(
        id="q1",
        kind="catalog_ref",
        prompt="Pick",
        catalog_type="biomarker",
    )
    candidates = await _snapshot_catalog_candidates(ctx, q)
    assert candidates == []


@pytest.mark.asyncio
async def test_catalog_ref_snapshot_swallows_search_errors():
    """The snapshot must never raise — a search failure degrades to []."""
    ctx = _ctx()
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        from app.ai.tools.ask_user import _snapshot_catalog_candidates, CatalogRefQuestion

        q = CatalogRefQuestion(
            id="q1",
            kind="catalog_ref",
            prompt="Pick",
            catalog_type="biomarker",
            prefilter={"query": "x"},
        )
        candidates = await _snapshot_catalog_candidates(ctx, q)
    assert candidates == []


@pytest.mark.asyncio
async def test_instance_ref_snapshot_clinical_event():
    """The instance_ref builder is invoked through the registry and its
    CandidateRef list is attached to the question."""
    from app.ai.tools.ask_user import (
        CandidateRef,
        _populate_candidates,
        InstanceRefQuestion,
    )

    expected = [
        CandidateRef(id="e1", name="Pregnancy", type="clinical_event"),
        CandidateRef(id="e2", name="Migraines", type="clinical_event"),
    ]
    builder_calls: list = []

    async def _fake(ctx):
        builder_calls.append(ctx)
        return expected

    ctx = _ctx()
    with patch.dict(
        "app.ai.tools.ask_user.INSTANCE_SNAPSHOT_BUILDERS",
        {"clinical_event": _fake},
    ):
        q = InstanceRefQuestion(
            id="q1", kind="instance_ref", prompt="Pick", entity_type="clinical_event"
        )
        await _populate_candidates(ctx, q)

    assert builder_calls == [ctx]
    assert q.candidates == expected


@pytest.mark.asyncio
async def test_instance_ref_unknown_entity_type_returns_empty_candidates():
    """An entity_type with no registered builder degrades to [] — never raises."""
    from app.ai.tools.ask_user import _populate_candidates, InstanceRefQuestion

    ctx = _ctx()
    q = InstanceRefQuestion(
        id="q1", kind="instance_ref", prompt="Pick", entity_type="clinical_event"
    )
    with patch.dict(
        "app.ai.tools.ask_user.INSTANCE_SNAPSHOT_BUILDERS", {}, clear=True,
    ):
        await _populate_candidates(ctx, q)
    assert q.candidates == []


@pytest.mark.asyncio
async def test_populate_candidates_does_not_overwrite_existing_snapshot():
    """If the LLM (or a future caller) already populated `candidates`, the
    tool does not re-snapshot."""
    from app.ai.tools.ask_user import (
        CandidateRef,
        _populate_candidates,
        CatalogRefQuestion,
    )

    ctx = _ctx()
    pre = [CandidateRef(id="x", name="Pre-set", type="biomarker")]
    q = CatalogRefQuestion(
        id="q1",
        kind="catalog_ref",
        prompt="Pick",
        catalog_type="biomarker",
        candidates=pre,
    )
    with patch(
        "app.ai.tools.ask_user._snapshot_catalog_candidates",
        new=AsyncMock(),
    ) as m:
        await _populate_candidates(ctx, q)
    assert q.candidates == pre  # untouched
    assert m.await_count == 0


# ---------------------------------------------------------------------------
# 4. Tool end-to-end (factory + __hitl__ marker)
# ---------------------------------------------------------------------------


def test_factory_returns_one_tool():
    tools = build(_ctx())
    assert len(tools) == 1
    assert tools[0].name == "ask_user"


@pytest.mark.asyncio
async def test_tool_returns_hitl_payload_on_valid_batch():
    """The tool must return ``{"__hitl__": True, "task": ...}`` for a valid
    payload, and the result must be parseable by ``_parse_hitl_proposal``."""
    ctx = _ctx()
    (ask_user,) = build(ctx)
    raw = await ask_user.ainvoke(
        {
            "questions": [
                {
                    "id": "q_dose",
                    "kind": "freetext",
                    "prompt": "What dosage?",
                    "placeholder": "500 mg",
                    "required": True,
                }
            ],
            "title": "Before I prescribe",
            "summary": "I need one detail.",
        }
    )
    parsed = json.loads(raw)
    assert parsed["__hitl__"] is True
    task = parsed["task"]
    assert task["task_type"] == "ask_user"
    assert task["title"] == "Before I prescribe"
    assert task["status"] == "proposed"
    assert task["proposed_payload"]["summary"] == "I need one detail."
    assert task["proposed_payload"]["questions"][0]["id"] == "q_dose"
    assert task["proposal_id"]  # uuid4 string

    # The chat loop's detector must recognise the payload.
    detected = _parse_hitl_proposal(raw)
    assert detected is not None
    assert detected["task_type"] == "ask_user"


@pytest.mark.asyncio
async def test_tool_returns_error_on_invalid_payload():
    """Invalid payloads come back as ``{"error": "..."}`` (NOT __hitl__) so
    the LLM can self-correct on the next iteration."""
    ctx = _ctx()
    (ask_user,) = build(ctx)
    raw = await ask_user.ainvoke(
        {"questions": [{"id": "q1", "kind": "rating", "prompt": "Stars"}]}
    )
    parsed = json.loads(raw)
    assert "error" in parsed
    assert "__hitl__" not in parsed


@pytest.mark.asyncio
async def test_tool_populates_candidates_for_catalog_ref():
    """End-to-end: catalog_ref with prefilter.query yields a payload whose
    question carries a non-empty candidates list."""
    ctx = _ctx()
    fake_hits = [{"id": "u1", "label": "HbA1c", "type": "biomarker"}]
    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=fake_hits),
    ):
        (ask_user,) = build(ctx)
        raw = await ask_user.ainvoke(
            {
                "questions": [
                    {
                        "id": "q1",
                        "kind": "catalog_ref",
                        "prompt": "Pick the affected biomarker",
                        "catalog_type": "biomarker",
                        "prefilter": {"query": "hba1c"},
                    }
                ]
            }
        )
    parsed = json.loads(raw)
    candidates = parsed["task"]["proposed_payload"]["questions"][0]["candidates"]
    assert candidates
    assert candidates[0]["id"] == "u1"
    assert candidates[0]["name"] == "HbA1c"


@pytest.mark.asyncio
async def test_tool_payload_serializes_cleanly_via_model_dump():
    """Pydantic ``model_dump(exclude_none=True, mode='json')`` must produce
    JSON-safe primitives (no Decimal, no datetime, no enums)."""
    typed = _validate_batch(
        [
            {
                "id": "q1",
                "kind": "multi_choice",
                "prompt": "Pick",
                "options": [{"value": "a", "label": "A"}],
                "default": ["a"],
            }
        ]
    )
    payload = [_question_to_payload(q) for q in typed]
    # Round-trip through json.dumps to prove JSON-safety.
    json.dumps(payload)
    assert payload[0]["default"] == ["a"]


# ---------------------------------------------------------------------------
# 5. Resolution summary (hitl.py extension)
# ---------------------------------------------------------------------------


def test_resolution_summary_formats_ask_user_freetext_answers():
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Quick questions",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_dose": "500 mg twice daily",
                        "q_reason": "Type 2 diabetes",
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "[HITL RESOLUTION FEEDBACK]" in out
    assert "1 confirmed" in out
    # Flat per-line format (not nested JSON).
    assert "q_dose: 500 mg twice daily" in out
    assert "q_reason: Type 2 diabetes" in out


def test_resolution_summary_formats_ask_user_choice_answers():
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_route": "oral",
                        "q_related": ["t2d", "hba1c"],
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "q_route: oral" in out
    # Multi-choice answers render as a comma-separated list.
    assert "t2d" in out and "hba1c" in out


def test_resolution_summary_formats_ask_user_ref_answers():
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_bio": {"id": "u1", "name": "HbA1c", "type": "biomarker"},
                        "q_multi_bio": [
                            {"id": "u1", "name": "HbA1c", "type": "biomarker"},
                            {"id": "u2", "name": "Glucose", "type": "biomarker"},
                        ],
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    # Flat per-line format: each answer on its own line as a JSON object.
    assert "q_bio:" in out
    assert "HbA1c" in out
    assert "Glucose" in out


def test_resolution_summary_trims_long_freetext_answer():
    """Long free-text answers are trimmed so the resume prompt stays small."""
    long_text = "x" * 1000
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {"final_payload": {"answers": {"q_note": long_text}}},
        }
    ]
    out = _hitl_resolution_summary(tasks)
    # The trimming uses the FREETEXT_ANSWER_TRIM_CHARS cap.
    from app.ai.tools.ask_user import FREETEXT_ANSWER_TRIM_CHARS

    assert "…" in out
    # No more than `cap + small overhead` chars between the q_note= prefix
    # and the closing bracket.
    assert long_text not in out  # the full 1000-char string must not appear
    assert FREETEXT_ANSWER_TRIM_CHARS < 1000


def test_resolution_summary_ask_user_dismissed_uses_default_path():
    """Dismissed ask_user tasks surface as DISMISSED (no answers to format)."""
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "dismissed",
            "resolved": {},
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "1 dismissed" in out
    assert "DISMISSED by the user" in out


# ---------------------------------------------------------------------------
# 6. Rich candidate fields in resolution summary
# ---------------------------------------------------------------------------


def test_resolution_summary_surfaces_all_rich_candidate_fields():
    """A catalog_ref/instance_ref answer's rich metadata (code, coding_system,
    category, is_telemetry, unit, description) must surface to the LLM so it
    can act on the pick WITHOUT a follow-up fetch."""
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick a biomarker",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_bio": {
                            "id": "u1",
                            "name": "HbA1c",
                            "slug": "hba1c",
                            "type": "biomarker",
                            "code": "4548-4",
                            "coding_system": "loinc",
                            "category": "Hematology",
                            "is_telemetry": False,
                            "unit": "%",
                            "description": "Glycated hemoglobin",
                        }
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    # Identity
    assert "HbA1c" in out
    assert "hba1c" in out
    # Coding / classification
    assert "4548-4" in out
    assert "loinc" in out
    assert "Hematology" in out
    # Type-specific
    assert "is_telemetry" in out
    assert "%" in out
    # Description
    assert "Glycated hemoglobin" in out


def test_resolution_summary_caps_oversized_candidate_field_values():
    """Per-field length caps keep the resume prompt within a sane budget even
    when an upstream adapter ships a huge description."""
    long_description = "y" * 1000
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q": {
                            "id": "u1",
                            "name": "X",
                            "type": "medication",
                            "description": long_description,
                        }
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "…" in out
    # The full 1000-char string must NOT appear in the summary.
    assert long_description not in out


def test_resolution_summary_drops_empty_candidate_fields():
    """None/empty fields are stripped so the LLM summary stays compact."""
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q": {
                            "id": "u1",
                            "name": "Pregnancy",
                            "type": "clinical_event",
                            "code": None,           # not set on instances
                            "coding_system": None,
                            "is_telemetry": None,
                            "unit": None,
                            "status": "ACTIVE",
                            "date": "2026-01-01",
                        }
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "Pregnancy" in out
    assert "ACTIVE" in out
    assert "2026-01-01" in out
    # The empty fields must not leak into the summary as JSON nulls.
    assert "code" not in out
    assert "unit" not in out
    assert "is_telemetry" not in out


def test_resolution_summary_instance_ref_answer_carries_date_and_status():
    """instance_ref answers surface date + status so the LLM can disambiguate
    'the surgery recovery last year' vs 'the one in 2024' without re-fetching."""
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick an event",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_event": {
                            "id": "e1",
                            "name": "Knee Surgery Recovery",
                            "type": "clinical_event",
                            "status": "RESOLVED",
                            "date": "2024-03-15",
                        }
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    assert "Knee Surgery Recovery" in out
    assert "RESOLVED" in out
    assert "2024-03-15" in out


def test_resolution_summary_multi_ref_answer_preserves_rich_fields_per_item():
    """multi-choice ref answers (array of candidates) preserve each item's
    rich fields, not just identification."""
    tasks = [
        {
            "task_type": "ask_user",
            "title": "Pick biomarkers",
            "proposal_id": "abcd1234efef",
            "status": "confirmed",
            "resolved": {
                "final_payload": {
                    "answers": {
                        "q_bios": [
                            {
                                "id": "u1",
                                "name": "HbA1c",
                                "type": "biomarker",
                                "code": "4548-4",
                                "coding_system": "loinc",
                                "is_telemetry": False,
                                "unit": "%",
                            },
                            {
                                "id": "u2",
                                "name": "Heart Rate",
                                "type": "biomarker",
                                "is_telemetry": True,
                                "unit": "bpm",
                            },
                        ]
                    }
                },
            },
        }
    ]
    out = _hitl_resolution_summary(tasks)
    # Both items present with their type-specific flags.
    assert "HbA1c" in out
    assert "Heart Rate" in out
    assert "is_telemetry" in out  # appears twice with different values
    assert "bpm" in out
    assert "4548-4" in out

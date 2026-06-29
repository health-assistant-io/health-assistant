"""Unit tests for the extracted provider-helpers (build_workflows).

``resolve_active_assignment`` is exercised end-to-end by the integration tests
(test_ai_config, test_ai_provider_access_control) via
``AIProviderService.get_active_assignment_for_task``; ``build_workflows`` is a
pure function and gets its own fast unit tests here.
"""
from app.ai.providers.workflows import build_workflows


class _Task:
    """Lightweight stand-in for a TaskTypeAssignment (truthy, identity-equal)."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Task {self.name}>"


def test_empty_assignments_yield_empty_workflow_lists():
    wf = build_workflows({})
    for v in wf.values():
        assert v == []


def test_full_reconstruction_combines_ocr_and_nlp_in_order():
    ocr, nlp = _Task("ocr"), _Task("nlp")
    wf = build_workflows({"ocr": ocr, "nlp": nlp})
    assert wf["full_reconstruction"] == [ocr, nlp]


def test_full_reconstruction_skips_missing_constituents():
    nlp = _Task("nlp")
    wf = build_workflows({"nlp": nlp})  # no ocr configured
    assert wf["full_reconstruction"] == [nlp]
    assert wf["fast_extraction"] == [nlp]


def test_single_constituent_workflows():
    chat = _Task("chat")
    wf = build_workflows({"chat": chat})
    assert wf["clinical_chat"] == [chat]
    # workflows unrelated to chat stay empty
    assert wf["magic_fill"] == []
    assert wf["medication_audit"] == []


def test_all_expected_workflow_keys_present():
    wf = build_workflows({})
    assert set(wf.keys()) == {
        "full_reconstruction",
        "fast_extraction",
        "smart_extraction_upload",
        "magic_fill",
        "clinical_chat",
        "medication_audit",
        "biomarker_definition",
        "medication_definition",
    }


def test_none_values_are_skipped():
    # A task_type key present but None (no assignment resolved) must not
    # produce a [None] entry in any workflow.
    wf = build_workflows({"ocr": None, "nlp": None})
    assert wf["full_reconstruction"] == []

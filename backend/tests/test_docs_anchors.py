"""The docs' code maps and status vocabulary stay tied to real code.

``docs/EXTRACTION_PIPELINE.md``, ``docs/ONTOLOGY_CATALOG.md`` and
``docs/STATE_BIOMARKERS.md`` each anchor their claims to a module + symbol
instead of a line number. Symbols still move (renames, moves between modules),
and the extraction page's status table can drift from the statuses the pipeline
actually sets — so both are machine-checked here rather than trusted. The pages
state that this test exists, which is only true while it does.
"""

from pathlib import Path

from scripts.check_docs_anchors import (
    LEGACY_STATUSES,
    PAGES,
    check,
    check_page,
    code_set_statuses,
    documented_statuses,
    rows_from_text,
    status_violations,
    violations,
)

REPO = Path(__file__).resolve().parents[2]
EXTRACTION = REPO / "docs/EXTRACTION_PIPELINE.md"


def _text(page: str) -> str:
    return (REPO / page).read_text(encoding="utf-8")


def test_code_maps_resolve():
    problems = check(REPO)
    assert problems == [], "\n".join(problems)


def test_every_opted_in_page_carries_a_real_map():
    """An empty or gutted map must not pass by resolving nothing."""
    for page in PAGES:
        rows = rows_from_text(_text(page))
        assert len(rows) >= 5, f"{page} anchors only {len(rows)} symbols"
        assert check_page(REPO, page) == []


def test_the_three_ai_mental_pages_stay_opted_in():
    """Dropping a page from the gate is the quiet way to lose the gate."""
    assert {
        "docs/EXTRACTION_PIPELINE.md",
        "docs/ONTOLOGY_CATALOG.md",
        "docs/STATE_BIOMARKERS.md",
    } <= set(PAGES)


def test_code_map_covers_the_pipeline_stages():
    symbols = {symbol for symbol, _ in rows_from_text(_text("docs/EXTRACTION_PIPELINE.md"))}
    for expected in (
        "ocr_document",
        "_check_trigger_cumulative",
        "MedicalProcessingService.run_extraction_pipeline",
        "LangChainStructuredExtractor.parse_document_pass_1",
        "process_unknown_biomarkers",
        "persist_results",
        "correct_one_sided_range",
    ):
        assert expected in symbols, f"{expected} is no longer anchored in the code map"


def test_documented_statuses_are_the_statuses_the_backend_sets():
    documented = documented_statuses(EXTRACTION.read_text(encoding="utf-8"))
    assigned = code_set_statuses(REPO / "backend/app")
    assert documented, "the examination-status table disappeared"
    assert status_violations(documented, assigned) == []
    assert sorted(documented) == sorted(assigned)


def test_legacy_statuses_are_not_presented_as_current():
    assert not set(documented_statuses(EXTRACTION.read_text(encoding="utf-8"))) & LEGACY_STATUSES
    for legacy in LEGACY_STATUSES:
        assert legacy in EXTRACTION.read_text(encoding="utf-8"), (
            f"{legacy} is no longer mentioned even as history — the page lost the note"
        )


def test_status_gate_catches_drift_both_ways():
    invented = status_violations(["phase_two"], {"processing", "completed"})
    assert any("never set by the backend" in problem for problem in invented)
    assert any("omits it" in problem for problem in invented)
    legacy_row = status_violations(["aggregating", "processing"], {"processing"})
    assert any("legacy status" in problem for problem in legacy_row)


def test_the_gate_catches_a_stale_anchor():
    """Guard against a checker that silently passes everything."""
    missing_symbol = violations(
        [("definitelyNotAFunction", "backend/app/workers/ai_tasks.py")], REPO
    )
    missing_module = violations([("ocr_document", "backend/app/workers/nope.py")], REPO)
    assert missing_symbol and "not defined" in missing_symbol[0]
    assert missing_module and "module not found" in missing_module[0]

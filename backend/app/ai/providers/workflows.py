"""Abstract workflow compositions for the AI config summary UI.

A "workflow" is a derived, frontend-facing grouping of one or more task-type
assignments (e.g. ``full_reconstruction`` = OCR + NLP). Extracted from
``AIProviderService.get_config_summary`` so the composition table is defined
in one place and can be unit-tested without a DB.
"""

from __future__ import annotations

from typing import Any, Dict, List


def build_workflows(task_assignments: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Derive the workflow -> task-assignment list map.

    ``task_assignments`` is keyed by task_type string ("ocr", "nlp", ...) and
    each value is a ``TaskTypeAssignment`` (or None). Workflows silently skip
    task types that have no assignment, so a workflow is empty when none of
    its constituent tasks are configured.
    """
    return {
        "full_reconstruction": [
            ta
            for ta in [task_assignments.get("ocr"), task_assignments.get("nlp")]
            if ta
        ],
        "fast_extraction": [ta for ta in [task_assignments.get("nlp")] if ta],
        "smart_extraction_upload": [ta for ta in [task_assignments.get("ocr")] if ta],
        "magic_fill": [
            ta for ta in [task_assignments.get("magic_fill_examination")] if ta
        ],
        "clinical_chat": [ta for ta in [task_assignments.get("chat")] if ta],
        "voice_input": [ta for ta in [task_assignments.get("transcription")] if ta],
        "medication_audit": [
            ta for ta in [task_assignments.get("medication_interaction")] if ta
        ],
        "biomarker_definition": [
            ta for ta in [task_assignments.get("define_biomarker")] if ta
        ],
        "medication_definition": [
            ta for ta in [task_assignments.get("define_medication")] if ta
        ],
    }

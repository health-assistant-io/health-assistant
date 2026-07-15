"""Obvious CHECK constraints (audit B10)

Revision ID: k2b3c4d5e6f7
Revises: j1a2b3c4d5e6
Create Date: 2026-07-15

The schema had exactly one CHECK constraint (``mrn_not_empty``). This adds the
obvious domain invariants that were previously enforced only by convention /
application code:

* ``units.conversion_multiplier > 0`` — a zero/negative multiplier breaks unit
  normalization (division / inversion).
* ``biomarker_definitions.reference_range_min <= reference_range_max`` (NULLs
  allowed) — an inverted range yields a nonsensical ``relative_score``.
* ``examinations.extraction_progress`` / ``documents.progress`` /
  ``export_jobs.progress`` / ``import_jobs.progress`` in ``[0, 100]`` — UI
  progress bars assume a percentage.
* ``ai_models.max_tokens > 0`` and ``ai_models.temperature BETWEEN 0 AND 2`` —
  an LLM call with ``max_tokens <= 0`` errors; temperature outside ``[0, 2]``
  is rejected by most OpenAI-compatible APIs.

The upgrade first clamps any pre-existing rows that would violate the new
constraints (defensive — the project has no users, but a dev DB may carry seed
data). The downgrade drops the constraints this migration introduced.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "k2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "j1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (constraint_name, table, condition) — created in upgrade(), dropped in
# downgrade(). Keep these in lock-step with the ``CheckConstraint`` entries on
# the SQLAlchemy models so autogenerate stays in sync.
_CONSTRAINTS = [
    ("ck_units_positive_conversion_multiplier", "units", "conversion_multiplier > 0"),
    (
        "ck_biomarker_definitions_ref_range_order",
        "biomarker_definitions",
        "reference_range_min IS NULL "
        "OR reference_range_max IS NULL "
        "OR reference_range_min <= reference_range_max",
    ),
    (
        "ck_examinations_extraction_progress_bounds",
        "examinations",
        "extraction_progress BETWEEN 0 AND 100",
    ),
    ("ck_documents_progress_bounds", "documents", "progress BETWEEN 0 AND 100"),
    ("ck_export_jobs_progress_bounds", "export_jobs", "progress BETWEEN 0 AND 100"),
    ("ck_import_jobs_progress_bounds", "import_jobs", "progress BETWEEN 0 AND 100"),
    ("ck_ai_models_positive_max_tokens", "ai_models", "max_tokens > 0"),
    (
        "ck_ai_models_temperature_bounds",
        "ai_models",
        "temperature BETWEEN 0 AND 2",
    ),
]


def upgrade() -> None:
    # --- normalize any pre-existing rows so the ADD CONSTRAINT cannot fail ---
    # Clamp percentage columns into [0, 100].
    for table in ("documents", "export_jobs", "import_jobs"):
        op.execute(
            f"UPDATE {table} SET progress = 100 WHERE progress > 100"
        )
        op.execute(f"UPDATE {table} SET progress = 0 WHERE progress < 0")
    op.execute(
        "UPDATE examinations SET extraction_progress = 100 "
        "WHERE extraction_progress > 100"
    )
    op.execute(
        "UPDATE examinations SET extraction_progress = 0 "
        "WHERE extraction_progress < 0"
    )

    # units: a non-positive / NULL multiplier is meaningless → default 1.0.
    op.execute(
        "UPDATE units SET conversion_multiplier = 1.0 "
        "WHERE conversion_multiplier IS NULL OR conversion_multiplier <= 0"
    )

    # ai_models: repair nonsensical LLM params to safe defaults.
    op.execute(
        "UPDATE ai_models SET max_tokens = 65536 "
        "WHERE max_tokens IS NULL OR max_tokens <= 0"
    )
    op.execute(
        "UPDATE ai_models SET temperature = 0 WHERE temperature IS NULL OR temperature < 0"
    )
    op.execute("UPDATE ai_models SET temperature = 2 WHERE temperature > 2")

    # biomarker_definitions: an inverted reference range is unusable; null both
    # ends so the row no longer carries a misleading range.
    op.execute(
        "UPDATE biomarker_definitions SET reference_range_min = NULL, "
        "reference_range_max = NULL "
        "WHERE reference_range_min IS NOT NULL AND reference_range_max IS NOT NULL "
        "AND reference_range_min > reference_range_max"
    )

    for name, table, condition in _CONSTRAINTS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for name, _table, _condition in reversed(_CONSTRAINTS):
        op.drop_constraint(name, _table, type_="check")

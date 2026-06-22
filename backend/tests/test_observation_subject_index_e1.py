"""Tests for audit item E1: expression index on Observation.subject.

The most-used query pattern in the codebase is
``Observation.subject["reference"].astext == "Patient/<uuid>"`` (12+ call
sites). Without an expression index on ``(subject->>'reference')`` every
per-patient observation query is a full scan within tenant.
"""
from sqlalchemy import create_engine, text

from app.core.config import settings


def _index_exists(table: str, index_name: str) -> bool:
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE tablename = :t AND indexname = :i"
                ),
                {"t": table, "i": index_name},
            ).scalar()
            return bool(result)
    finally:
        engine.dispose()


def test_observation_subject_ref_index_exists():
    """The expression index on fhir_observations must exist."""
    assert _index_exists("fhir_observations", "ix_fhir_observations_subject_ref"), (
        "Expression index ix_fhir_observations_subject_ref is missing — "
        "every per-patient observation query will full-scan"
    )


def test_diagnostic_report_subject_ref_index_exists():
    """The expression index on fhir_diagnostic_reports must exist."""
    assert _index_exists(
        "fhir_diagnostic_reports", "ix_fhir_diagnostic_reports_subject_ref"
    ), (
        "Expression index ix_fhir_diagnostic_reports_subject_ref is missing"
    )


def test_observation_subject_ref_index_is_used_by_planner():
    """EXPLAIN of a subject->>'reference' lookup must show an Index Scan,
    proving the planner picks up the expression index."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            plan = conn.execute(
                text(
                    "EXPLAIN (COSTS OFF) SELECT * FROM fhir_observations "
                    "WHERE subject->>'reference' = 'Patient/test'"
                )
            ).all()
            plan_text = " ".join(row[0] for row in plan)
            assert "Index Scan" in plan_text, (
                f"Planner did not choose the expression index. Plan: {plan_text}"
            )
    finally:
        engine.dispose()


def test_model_declares_expression_index():
    """The ObservationModel.__table_args__ must include the expression index."""
    from app.models.fhir.patient import Observation

    index_names = {idx.name for idx in Observation.__table__.indexes}
    assert "ix_fhir_observations_subject_ref" in index_names, (
        f"ObservationModel is missing the expression index. "
        f"Got: {sorted(index_names)}"
    )

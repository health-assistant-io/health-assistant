"""Tests for audit item D9: Patient deletion cascade symmetry.

Policy: deleting a Patient removes their ENTIRE clinical record. Previously
the schema was asymmetric — CASCADE on meds/allergies/events but SET NULL
on exams/documents/devices/chat_sessions, leaving orphaned rows. All
patient_id FKs must now use CASCADE.
"""
from sqlalchemy import create_engine, text

from app.core.config import settings


def _patient_fk_cascade_rules():
    """Return {table_name: delete_rule} for every patient_id FK."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT conrelid::regclass::text AS table_name, confdeltype
                    FROM pg_constraint
                    WHERE contype = 'f'
                      AND conrelid IN (
                          SELECT oid FROM pg_class WHERE relname IN (
                              SELECT table_name
                              FROM information_schema.columns
                              WHERE column_name = 'patient_id'
                                AND table_schema = 'public'
                          )
                      )
                      AND conname LIKE '%patient_id%'
                    ORDER BY table_name
                    """
                )
            ).all()
            return {row[0]: row[1] for row in rows}
    finally:
        engine.dispose()


def test_all_patient_fks_use_cascade():
    """Every patient_id FK must use ON DELETE CASCADE (confdeltype = 'c')).

    The four tables that previously used SET NULL (chat_sessions, documents,
    examinations, fhir_devices) are the regression target.
    """
    rules = _patient_fk_cascade_rules()
    non_cascade = {
        t: r for t, r in rules.items() if r != "c"
    }
    assert not non_cascade, (
        f"Tables with patient_id FK NOT using CASCADE: {non_cascade}. "
        "All patient-owned data must cascade-delete with the patient."
    )


def test_previously_set_null_tables_now_cascade():
    """The 4 tables that had SET NULL must now have CASCADE."""
    rules = _patient_fk_cascade_rules()
    previously_set_null = [
        "chat_sessions",
        "documents",
        "examinations",
        "fhir_devices",
    ]
    for table in previously_set_null:
        rule = rules.get(table)
        assert rule == "c", (
            f"{table} patient_id FK must be CASCADE (was SET NULL before D9 fix), "
            f"got confdeltype={rule!r}"
        )


def test_model_declarations_use_cascade():
    """Static check: the 4 model files must declare ondelete='CASCADE'
    on their patient_id column (not SET NULL)."""
    from pathlib import Path

    models_dir = Path(__file__).resolve().parents[1] / "app" / "models"
    model_files = {
        "document_model.py": models_dir / "document_model.py",
        "examination_model.py": models_dir / "examination_model.py",
        "device.py": models_dir / "fhir" / "device.py",
        "chat_model.py": models_dir / "chat_model.py",
    }
    for label, path in model_files.items():
        src = path.read_text()
        assert 'ForeignKey("fhir_patients.id", ondelete="CASCADE")' in src, (
            f"{path.name} must declare ondelete='CASCADE' on patient_id FK"
        )
        assert 'ForeignKey("fhir_patients.id", ondelete="SET NULL")' not in src, (
            f"{path.name} must NOT use ondelete='SET NULL' on patient_id FK"
        )

"""0025 adds only the nullable key column — it must not backfill keys.

A backfilled row with a missing object makes the worker prefer an unreadable
key. The key copy/backfill belongs to the migration tool, so this revision is
DDL-only. These tests pin that contract at the revision boundary.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0025_document_object_key.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_0025", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeOp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.bind_requested = False

    def add_column(self, table: str, column) -> None:
        self.calls.append(("add_column", table, column.name, column.nullable))

    def create_index(self, name: str, table: str, columns: list[str], **kwargs) -> None:
        self.calls.append(("create_index", name, table, tuple(columns), kwargs))

    def drop_index(self, name: str, **kwargs) -> None:
        self.calls.append(("drop_index", name, kwargs.get("table_name")))

    def drop_column(self, table: str, name: str) -> None:
        self.calls.append(("drop_column", table, name))

    def get_bind(self):
        self.bind_requested = True
        raise AssertionError("0025 must not touch rows")


def test_upgrade_adds_only_a_nullable_indexed_column(monkeypatch) -> None:
    migration = _load()
    fake = _FakeOp()
    monkeypatch.setattr(migration, "op", fake)
    migration.upgrade()
    assert fake.bind_requested is False
    assert fake.calls == [
        ("add_column", "documents", "object_key", True),
        (
            "create_index",
            "ix_documents_object_key",
            "documents",
            ("object_key",),
            {"unique": False},
        ),
    ]


def test_downgrade_drops_the_index_and_column(monkeypatch) -> None:
    migration = _load()
    fake = _FakeOp()
    monkeypatch.setattr(migration, "op", fake)
    migration.downgrade()
    assert fake.calls == [
        ("drop_index", "ix_documents_object_key", "documents"),
        ("drop_column", "documents", "object_key"),
    ]


def test_revision_contains_no_backfill_sql() -> None:
    source = MIGRATION.read_text()
    assert "UPDATE documents" not in source
    assert "SELECT id, tenant_id, filename" not in source
    assert "object_key IS NULL" not in source

"""Catalog search surfaces and the index DDL they imply (ADR 0055 Phase 4).

A catalog's searchable text is data: each descriptor declares its full-text
columns (``search_fts_columns``), the subset that gets a per-column trigram GIN
index (``search_trigram_columns``) and the JSONB members that need a ``::text``
cast (``search_json_columns``). This module turns that data into the one FTS
expression the query layer matches on and the index statements Postgres needs,
so an index and the query that is supposed to use it can never drift apart.

The migration lane freezes the surfaces it created (``0024_catalog_cleanup``)
and ``tests/test_catalog_search_index.py`` asserts the freeze equals the live
registry: adding a searched column without a migration fails the gate.

This module is deliberately import-free of the application package graph —
migrations import it directly (the same reason
:mod:`app.core.catalog_identity` carries the identity constants).
"""

from __future__ import annotations

from dataclasses import dataclass, field

FTS_CONFIG = "simple"


@dataclass(frozen=True)
class CatalogSearchSurface:
    """One table's search surface: the columns and the indexes over them.

    ``fts_columns`` is ordered and its first entry is the catalog's display
    label (the trigram similarity target and the ``ts_headline`` source when
    no richer column exists). ``trigram_columns`` are the extra text columns
    beyond the label; ``json_columns`` are searched through their ``::text``
    projection.
    """

    catalog_type: str
    table: str
    fts_columns: tuple[str, ...]
    trigram_columns: tuple[str, ...] = ()
    json_columns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label_column(self) -> str:
        return self.fts_columns[0]

    @property
    def indexed_trigram_columns(self) -> tuple[str, ...]:
        return (self.label_column, *self.trigram_columns)

    def fts_expression(self) -> str:
        """The ``to_tsvector`` body — identical in the index and the query."""
        return " || ' ' || ".join(f"coalesce({column}::text, '')" for column in self.fts_columns)

    def fts_index_name(self) -> str:
        return f"ix_{self.table}_fts"

    def trigram_index_name(self, column: str) -> str:
        return f"ix_{self.table}_{column}_trgm"

    def index_names(self) -> tuple[str, ...]:
        return (
            *(self.trigram_index_name(column) for column in self.indexed_trigram_columns),
            self.fts_index_name(),
        )

    def index_ddl(self) -> tuple[tuple[str, str], ...]:
        """``(name, statement)`` pairs, trigram indexes first then the FTS one."""
        statements: list[tuple[str, str]] = []
        for column in self.indexed_trigram_columns:
            name = self.trigram_index_name(column)
            statements.append(
                (name, f"CREATE INDEX {name} ON {self.table} USING GIN ({column} gin_trgm_ops)")
            )
        name = self.fts_index_name()
        statements.append(
            (
                name,
                f"CREATE INDEX {name} ON {self.table} USING GIN "
                f"(to_tsvector('{FTS_CONFIG}', {self.fts_expression()}))",
            )
        )
        return tuple(statements)

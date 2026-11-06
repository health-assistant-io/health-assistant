"""Autogenerate scope for the model⇄DB drift gate.

SQLAlchemy cannot express a handful of index shapes that the migration lane
creates as raw SQL:

* catalog search indexes with a non-default opclass
  (``gin_trgm_ops``) or a ``to_tsvector(...)`` expression,
* the per-tenant ``COALESCE(tenant_id, sentinel)`` uniques
  (``ix_*_slug_tenant`` / ``ix_fhir_patients_mrn_tenant``),
* ``ix_concept_edges_unique`` (same COALESCE form).

Autogenerate reflects those indexes but cannot match them against metadata,
so — without this list — every run would report them as removed and the drift
gate could never be green. The list is deliberately explicit and small:
anything else is model-owned DDL and must round-trip empty.

``tests/test_schema_parameters.py`` asserts every listed index actually exists
on the migrated database, so the exemption cannot silently hide a missing
index.
"""

MIGRATION_MANAGED_INDEXES = frozenset(
    {
        "ix_allergy_catalog_fts",
        "ix_allergy_catalog_name_trgm",
        "ix_anatomy_structures_fts",
        "ix_anatomy_structures_name_trgm",
        "ix_anatomy_structures_slug_tenant",
        "ix_anatomy_structures_slug_trgm",
        "ix_biomarker_definitions_fts",
        "ix_biomarker_definitions_name_trgm",
        "ix_biomarker_definitions_slug_tenant",
        "ix_biomarker_definitions_slug_trgm",
        "ix_clinical_event_types_slug_tenant",
        "ix_concept_edges_unique",
        "ix_concepts_aliases_gin",
        "ix_concepts_fts",
        "ix_concepts_name_trgm",
        "ix_concepts_slug_tenant",
        "ix_concepts_slug_trgm",
        "ix_concepts_trgm",
        "ix_fhir_observations_component_gin",
        "ix_fhir_patients_mrn_tenant",
        "ix_medication_catalog_fts",
        "ix_medication_catalog_name_trgm",
        "ix_vaccine_catalog_fts",
        "ix_vaccine_catalog_name_trgm",
    }
)

# Tables created at runtime by a library, outside the migration lane.
# ``AsyncPostgresSaver.setup()`` (LangGraph checkpointer) creates and migrates
# these at boot with idempotent DDL; their shape is owned by
# ``langgraph-checkpoint-postgres``, not by our models. Without this exemption
# the drift gate would fail on any database where a graph has already run —
# an ordering-dependent flake, not drift. ``app/ai/graphs/checkpointer.py``
# and its tests own their correctness.
RUNTIME_MANAGED_TABLES = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)


def include_object(object, name, type_, reflected, compare_to):
    """Alembic ``include_object`` hook: skip non-model-owned objects."""
    if type_ == "index" and name in MIGRATION_MANAGED_INDEXES:
        return False
    table_name = name if type_ == "table" else getattr(getattr(object, "table", None), "name", None)
    return table_name not in RUNTIME_MANAGED_TABLES

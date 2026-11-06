"""The one lifecycle registry: every mapped table declares its data class.

Constitution rule 10 (``guidelines/data-model.md`` §7): no table carries an
implied lifecycle. Each mapped table is classified exactly once as

* ``record`` — retained with the subject/record; erased only when the
  subject (patient, user or tenant) is erased. This is the plan's
  ``clinical-record`` class plus the reference/configuration rows that live
  as long as their subject;
* ``operational`` — bounded retention; a ``retention_policies`` row names the
  cutoff and a named job enforces it;
* ``transient`` — prunable at any time (runtime session state).

The registry is the code-side mirror of
``contracts/data-model.lifecycle.txt`` (the file gate D6 reads). Both are
part of the same commit: a new model without a class turns the gate red and
:func:`validate_lifecycle` returns the same finding here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "LIFECYCLE_CLASSES",
    "LifecycleClass",
    "LifecycleError",
    "UnclassifiedTable",
    "class_for",
    "operational_tables",
    "tables_in_class",
    "transient_tables",
    "validate_lifecycle",
]


class LifecycleClass(StrEnum):
    """The closed lifecycle vocabulary (CHECK: ``record|operational|transient``)."""

    RECORD = "record"
    OPERATIONAL = "operational"
    TRANSIENT = "transient"


class LifecycleError(ValueError):
    """Base class for lifecycle registry rejections."""


class UnclassifiedTable(LifecycleError):
    """A mapped table has no lifecycle class — a schema-doctrine bug."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(
            f"table {table!r} has no lifecycle class — add it to "
            "app.core.data_lifecycle.LIFECYCLE_CLASSES and "
            "contracts/data-model.lifecycle.txt"
        )


_CLASSES: dict[str, LifecycleClass] = {
    # --- identity + tenancy -------------------------------------------------
    "tenants": LifecycleClass.RECORD,
    "users": LifecycleClass.RECORD,
    "instance_settings": LifecycleClass.RECORD,
    "system_settings": LifecycleClass.RECORD,
    "system_integrations": LifecycleClass.RECORD,
    "oauth_clients": LifecycleClass.RECORD,
    "auth_sessions": LifecycleClass.TRANSIENT,
    # --- clinical record ----------------------------------------------------
    "fhir_patients": LifecycleClass.RECORD,
    "fhir_observations": LifecycleClass.RECORD,
    "fhir_medications": LifecycleClass.RECORD,
    "fhir_allergy_intolerances": LifecycleClass.RECORD,
    "fhir_diagnostic_reports": LifecycleClass.RECORD,
    "fhir_communications": LifecycleClass.RECORD,
    "fhir_devices": LifecycleClass.RECORD,
    "fhir_organizations": LifecycleClass.RECORD,
    "fhir_provenance": LifecycleClass.RECORD,
    "examinations": LifecycleClass.RECORD,
    "documents": LifecycleClass.RECORD,
    "clinical_events": LifecycleClass.RECORD,
    "clinical_event_occurrences": LifecycleClass.RECORD,
    "clinical_event_types": LifecycleClass.RECORD,
    "event_anatomy_links": LifecycleClass.RECORD,
    "event_examination_links": LifecycleClass.RECORD,
    "event_observation_links": LifecycleClass.RECORD,
    "patient_immunizations": LifecycleClass.RECORD,
    "patient_layouts": LifecycleClass.RECORD,
    "doctors": LifecycleClass.RECORD,
    "laboratories": LifecycleClass.RECORD,
    "biomarker_states": LifecycleClass.RECORD,
    "biomarker_definitions": LifecycleClass.RECORD,
    "biomarker_reference_ranges": LifecycleClass.RECORD,
    "biomarker_allowed_states": LifecycleClass.OPERATIONAL,
    "units": LifecycleClass.RECORD,
    "concepts": LifecycleClass.RECORD,
    "concept_edges": LifecycleClass.RECORD,
    "concept_kind_tags": LifecycleClass.RECORD,
    "allergy_catalog": LifecycleClass.RECORD,
    "medication_catalog": LifecycleClass.RECORD,
    "vaccine_catalog": LifecycleClass.RECORD,
    "anatomy_structures": LifecycleClass.RECORD,
    "anatomy_figures": LifecycleClass.RECORD,
    "chat_sessions": LifecycleClass.RECORD,
    "chat_messages": LifecycleClass.RECORD,
    "notification_rules": LifecycleClass.RECORD,
    "notification_triggers": LifecycleClass.RECORD,
    # --- user-owned configuration -------------------------------------------
    "user_integrations": LifecycleClass.RECORD,
    "integration_credentials": LifecycleClass.RECORD,
    "integration_sync_state": LifecycleClass.OPERATIONAL,
    # plan 52 / ADR-0052: bindings, links and sealed credentials are RECORD
    # (erased with the owning user/patient/binding); run logs are OPERATIONAL
    # (bounded by the retention job).
    "calendar_bindings": LifecycleClass.RECORD,
    "calendar_event_links": LifecycleClass.RECORD,
    "calendar_binding_credentials": LifecycleClass.RECORD,
    "calendar_sync_logs": LifecycleClass.OPERATIONAL,
    "export_jobs": LifecycleClass.OPERATIONAL,
    "import_jobs": LifecycleClass.OPERATIONAL,
    # --- operational history ------------------------------------------------
    "audit_events": LifecycleClass.OPERATIONAL,
    "catalog_audit_log": LifecycleClass.OPERATIONAL,
    "task_logs": LifecycleClass.OPERATIONAL,
    "ai_call_logs": LifecycleClass.OPERATIONAL,
    "ai_task_defs": LifecycleClass.OPERATIONAL,
    "ai_providers": LifecycleClass.RECORD,
    "ai_models": LifecycleClass.RECORD,
    "ai_task_assignments": LifecycleClass.RECORD,
    "proposal_kinds": LifecycleClass.OPERATIONAL,
    "proposals": LifecycleClass.OPERATIONAL,
    "notifications": LifecycleClass.OPERATIONAL,
    "notification_recipients": LifecycleClass.OPERATIONAL,
    "notification_deliveries": LifecycleClass.OPERATIONAL,
    "notification_subscriptions": LifecycleClass.OPERATIONAL,
    "mobile_push_targets": LifecycleClass.OPERATIONAL,
    "integration_sync_logs": LifecycleClass.OPERATIONAL,
    "integration_debug_logs": LifecycleClass.OPERATIONAL,
    "telemetry_data": LifecycleClass.OPERATIONAL,
    "telemetry_anomalies": LifecycleClass.OPERATIONAL,
    "telemetry_devices": LifecycleClass.OPERATIONAL,
    # --- data-lifecycle control plane --------------------------------------
    "erasure_requests": LifecycleClass.RECORD,
    "retention_policies": LifecycleClass.OPERATIONAL,
    # --- extension source lane (plan 7) -------------------------------------
    "extension_sources": LifecycleClass.RECORD,
    "extension_source_versions": LifecycleClass.RECORD,
    "extension_consents": LifecycleClass.RECORD,
    "extension_isolation_posture": LifecycleClass.OPERATIONAL,
}

LIFECYCLE_CLASSES: Mapping[str, LifecycleClass] = MappingProxyType(_CLASSES)


def class_for(table: str) -> LifecycleClass:
    """The declared lifecycle class for ``table`` (raises if unclassified)."""
    try:
        return LIFECYCLE_CLASSES[table]
    except KeyError as exc:
        raise UnclassifiedTable(table) from exc


def tables_in_class(lifecycle_class: LifecycleClass | str) -> frozenset[str]:
    """Every registered table in one class."""
    wanted = LifecycleClass(lifecycle_class)
    return frozenset(table for table, cls in LIFECYCLE_CLASSES.items() if cls is wanted)


def operational_tables() -> frozenset[str]:
    """Tables with bounded retention (the job-bound class)."""
    return tables_in_class(LifecycleClass.OPERATIONAL)


def transient_tables() -> frozenset[str]:
    """Prunable-at-any-time tables."""
    return tables_in_class(LifecycleClass.TRANSIENT)


def validate_lifecycle(tables: Iterable[str]) -> list[str]:
    """Return gate-shaped findings for every table without a class.

    This mirrors the ``FAIL D6`` findings of ``scripts/check-data-model.sh``
    so the in-suite gate and the vendored shell gate cannot disagree.
    """
    return [
        f"FAIL D6: unclassified table {table} — add to the lifecycle inventory "
        "with class record|operational|transient"
        for table in sorted(set(tables))
        if table not in LIFECYCLE_CLASSES
    ]

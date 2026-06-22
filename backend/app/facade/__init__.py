"""FHIR R4 facade layer.

This package implements the conformant FHIR R4 REST API at ``/api/v1/fhir/R4``.
It is the **interop surface only** — external systems (FHIR servers, HL7
importers, export/import jobs, SMART-on-FHIR clients). The frontend does not
use the facade; it speaks the domain endpoints (``/patients/*``,
``/observations/*``, ``/examinations/*``, ...) which return ORM-shape dicts
optimized for the UI. The facade layer:

* Accepts **canonical FHIR R4 JSON** on writes (validated by ``fhir.resources``)
* Returns **FHIR Bundles** on search (``type=searchset`` with pagination links)
* Honors standard search params (``_id``, ``_lastUpdated``, ``_count``,
  ``_sort``, ``_format``, ``_include``)
* Soft-deletes via ``SoftDeleteMixin`` (deleted resources → ``410 Gone``)
* Records a ``Provenance`` resource on every write

Resources are projected to FHIR by each model's ``to_fhir_dict()`` method —
there is no schema duplication or dual-write.
"""

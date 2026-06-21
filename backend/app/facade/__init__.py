"""FHIR R4 facade layer.

This package implements the conformant FHIR R4 REST API at ``/api/v1/fhir/R4``.
It is intentionally separate from the ORM-shape ``/api/v1/fhir/*`` router,
which the existing frontend depends on for snake_case ORM dicts. The facade
layer:

* Accepts **canonical FHIR R4 JSON** on writes (validated by ``fhir.resources``)
* Returns **FHIR Bundles** on search (``type=searchset`` with pagination links)
* Honors standard search params (``_id``, ``_lastUpdated``, ``_count``,
  ``_sort``, ``_format``, ``_include``)
* Soft-deletes via ``SoftDeleteMixin`` (deleted resources → ``410 Gone``)
* Records a ``Provenance`` resource on every write

The existing ORM-shape router stays untouched. Resources are projected to
FHIR by each model's ``to_fhir_dict()`` method — there is no schema
duplication or dual-write.
"""

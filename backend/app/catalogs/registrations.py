"""Register every clinical catalog with :class:`CatalogRegistry`.

Imported once via :mod:`app.catalogs.__init__`. Adding a catalog = appending one
``CatalogRegistry.register(...)`` call here (plus, for a new table, one model
+ one resolver function).

Phase 0 scope: the five existing catalogs. Vaccines (Phase 5) and diseases-as-
concepts (Phase 6) register here when they land. Resolvers for
medication/allergy/clinical_event_type arrive in Phase 2; until then their
``resolver`` stays ``None`` and the graph falls back to the ``{type}:{id}``
label.
"""

from __future__ import annotations

from app.catalogs.adapters import (
    AllergyCatalogAdapter,
    AnatomyCatalogAdapter,
    BiomarkerCatalogAdapter,
    ConceptCatalogAdapter,
    MedicationCatalogAdapter,
    VaccineCatalogAdapter,
)
from app.catalogs.descriptors import CatalogDescriptor
from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.catalogs.protocol import CatalogUiMeta, ConceptLink
from app.catalogs.registry import CatalogRegistry
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept
from app.models.enums import EdgeEndpointType
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.services.concept_endpoint_resolver import (
    _resolve_allergies,
    _resolve_anatomy,
    _resolve_biomarkers,
    _resolve_concepts,
    _resolve_medications,
    _resolve_vaccines,
)


def _register_all() -> None:
    CatalogRegistry.register(
        CatalogDescriptor(
            type="biomarker",
            model=BiomarkerDefinition,
            service=BiomarkerCatalogAdapter(),
            search_columns=("name", "slug", "aliases"),
            concept_link=ConceptLink(fk_column="class_concept_id"),
            edge_endpoint_type=EdgeEndpointType.BIOMARKER,
            resolver=_resolve_biomarkers,
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.biomarker.label",
                icon="Activity",
                color="violet",
                admin_route="/admin/catalogs/biomarker",
            ),
        )
    )
    CatalogRegistry.get("biomarker").service.catalog_type = "biomarker"

    CatalogRegistry.register(
        CatalogDescriptor(
            type="medication",
            model=MedicationCatalog,
            service=MedicationCatalogAdapter(),
            search_columns=("name", "indications", "description"),
            concept_link=ConceptLink(fk_column="class_concept_id"),
            edge_endpoint_type=EdgeEndpointType.MEDICATION,
            resolver=_resolve_medications,
            fhir_projector=lambda obj: obj.to_fhir_dict(),
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.medication.label",
                icon="Pill",
                color="blue",
                admin_route="/admin/catalogs/medication",
            ),
        )
    )
    CatalogRegistry.get("medication").service.catalog_type = "medication"

    CatalogRegistry.register(
        CatalogDescriptor(
            type="allergy",
            model=AllergyCatalog,
            service=AllergyCatalogAdapter(),
            search_columns=("name", "description"),
            concept_link=ConceptLink(fk_column="class_concept_id"),
            edge_endpoint_type=EdgeEndpointType.ALLERGY,
            resolver=_resolve_allergies,
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.allergy.label",
                icon="ShieldAlert",
                color="amber",
                admin_route="/admin/catalogs/allergy",
            ),
        )
    )
    CatalogRegistry.get("allergy").service.catalog_type = "allergy"

    CatalogRegistry.register(
        CatalogDescriptor(
            type="anatomy",
            model=AnatomyStructure,
            service=AnatomyCatalogAdapter(),
            search_columns=("name", "slug", "standard_code", "description"),
            concept_link=ConceptLink(fk_column="class_concept_id"),
            edge_endpoint_type=EdgeEndpointType.ANATOMY,
            resolver=_resolve_anatomy,
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.anatomy.label",
                icon="PersonStanding",
                color="emerald",
                admin_route="/admin/catalogs/anatomy",
            ),
        )
    )
    CatalogRegistry.get("anatomy").service.catalog_type = "anatomy"

    CatalogRegistry.register(
        CatalogDescriptor(
            type="vaccine",
            model=VaccineCatalog,
            service=VaccineCatalogAdapter(),
            search_columns=("name", "description", "code"),
            concept_link=ConceptLink(fk_column="class_concept_id"),
            edge_endpoint_type=EdgeEndpointType.IMMUNIZATION,
            resolver=_resolve_vaccines,
            fhir_projector=lambda obj: obj.to_fhir_dict(),
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.vaccine.label",
                icon="Syringe",
                color="rose",
                admin_route="/admin/catalogs/vaccine",
            ),
        )
    )
    CatalogRegistry.get("vaccine").service.catalog_type = "vaccine"

    CatalogRegistry.register(
        CatalogDescriptor(
            type="concept",
            model=Concept,
            service=ConceptCatalogAdapter(),
            search_columns=("name", "slug", "description"),
            concept_link=None,
            edge_endpoint_type=EdgeEndpointType.CONCEPT,
            resolver=_resolve_concepts,
            rbac=DEFAULT_CATALOG_POLICY,
            ui=CatalogUiMeta(
                label_key="catalogs.concept.label",
                icon="Network",
                color="slate",
                admin_route="/admin/system/taxonomy",
            ),
        )
    )
    CatalogRegistry.get("concept").service.catalog_type = "concept"

    # Stamp the concept-link FK column onto each adapter from its descriptor, so
    # the generic ``?class=<slug>`` filter (BaseCatalogAdapter) works for every
    # catalog that has a ``class_concept_id`` without per-adapter code.
    for desc in CatalogRegistry.all():
        if desc.concept_link:
            desc.service.concept_link_column = desc.concept_link.fk_column


_register_all()

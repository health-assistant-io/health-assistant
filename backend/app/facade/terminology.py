"""FHIR R4 terminology resources (``CodeSystem`` + ``ValueSet``) for the facade.

These are **computed** resources — they don't map 1:1 to a single table. Instead
they project from the ``concepts`` table (disease-kind concepts) as a single
aggregate FHIR resource. They're registered in :data:`RESOURCE_REGISTRY` with
``read_fn`` / ``search_fn`` hooks that bypass the generic table-query dispatcher.

Phase 6 publishes the curated disease reference (``kind=disease``, ICD-10 codes)
as a FHIR ``CodeSystem`` (the definitional vocabulary) and a ``ValueSet`` (a
selectable subset) so external FHIR clients can discover and validate disease
codes via the standard ``GET /fhir/R4/CodeSystem/{id}`` and
``GET /fhir/R4/ValueSet/{id}`` reads.

Both resources share the id ``ha-diseases`` and the canonical system URL
``http://hl7.org/fhir/sid/icd-10`` (the concepts' ``coding_system`` is
``icd10``). Adding another published vocabulary later = add another entry to
:data:`_PUBLISHED_VOCABULARIES` (the registry iterates it).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.facade.bundle import build_search_bundle
from app.models.concept_model import Concept
from app.models.enums import ConceptKind
from app.services.concept_service import concepts_with_kind
from app.services.fhir_helpers import build_fhir_resource


# The canonical FHIR system URL for the ICD-10 coding system.
_ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10"

# Stable resource id shared by the CodeSystem + ValueSet for the disease vocab.
_DISEASE_VOCAB_ID = "ha-diseases"


async def _load_disease_concepts(db: AsyncSession) -> List[Concept]:
    """Fetch all global, active disease concepts ordered by display name."""
    rows = (
        (
            await db.execute(
                select(Concept)
                .where(
                    concepts_with_kind(ConceptKind.DISEASE),
                    Concept.tenant_id.is_(None),
                    Concept.deleted_at.is_(None),
                    Concept.status == "active",
                )
                .order_by(Concept.name)
            )
        )
        .scalars()
        .all()
    )
    return rows


def _build_codesystem(concepts: List[Concept]) -> Dict[str, Any]:
    """Build a validated FHIR ``CodeSystem`` resource from disease concepts."""
    concept_entries = [
        {
            "code": c.code,
            "display": c.name,
            **({"definition": c.description} if c.description else {}),
        }
        for c in concepts
        if c.code  # skip any concept lacking a code
    ]
    raw = {
        "resourceType": "CodeSystem",
        "id": _DISEASE_VOCAB_ID,
        "url": f"urn:uuid:health-assistant:codesystem:{_DISEASE_VOCAB_ID}",
        "identifier": [{"system": "urn:ietf:rfc:3986", "value": _ICD10_SYSTEM}],
        "name": "HADiseasesICD10",
        "title": "Health Assistant Disease Reference (ICD-10)",
        "status": "active",
        "content": "complete",
        "count": len(concept_entries),
        "concept": concept_entries,
    }
    return build_fhir_resource("CodeSystem", raw)


def _build_valueset(concepts: List[Concept]) -> Dict[str, Any]:
    """Build a validated FHIR ``ValueSet`` resource from disease concepts."""
    concept_refs = [{"code": c.code, "display": c.name} for c in concepts if c.code]
    raw = {
        "resourceType": "ValueSet",
        "id": _DISEASE_VOCAB_ID,
        "url": f"urn:uuid:health-assistant:valueset:{_DISEASE_VOCAB_ID}",
        "name": "HADiseases",
        "title": "Health Assistant Disease Reference",
        "status": "active",
        "compose": {"include": [{"system": _ICD10_SYSTEM, "concept": concept_refs}]},
    }
    return build_fhir_resource("ValueSet", raw)


# ---------------------------------------------------------------------------
# Registry of published vocabularies
# ---------------------------------------------------------------------------

# Each entry: (resource_type, resource_id, builder). The builder takes the
# loaded concept list and returns a validated FHIR resource dict. ``read_fn``
# and ``search_fn`` consult this so adding a vocabulary = one entry here.
_PUBLISHED_VOCABULARIES: List[Tuple[str, str, Any]] = [
    ("CodeSystem", _DISEASE_VOCAB_ID, _build_codesystem),
    ("ValueSet", _DISEASE_VOCAB_ID, _build_valueset),
]


def _find(resource_type: str, resource_id: str) -> Optional[Any]:
    for rtype, rid, builder in _PUBLISHED_VOCABULARIES:
        if rtype == resource_type and rid == resource_id:
            return builder
    return None


# ---------------------------------------------------------------------------
# Facade hooks (signatures match ResourceEntry.read_fn / search_fn)
# ---------------------------------------------------------------------------


async def read_terminology(
    db: AsyncSession,
    resource_id: str,
    current_user,  # noqa: ARG001 — auth context unused; terminology is public read
    *,
    resource_type: str,
) -> Optional[Dict[str, Any]]:
    """Read one terminology resource by id. Returns None if unknown."""
    builder = _find(resource_type, resource_id)
    if builder is None:
        return None
    concepts = await _load_disease_concepts(db)
    return builder(concepts)


async def search_terminology(
    db: AsyncSession,  # noqa: ARG001
    query_params: List[Tuple[str, str]],
    current_user,  # noqa: ARG001
    base_url: str,
    *,
    resource_type: str,
) -> Dict[str, Any]:
    """Return a Bundle listing the published terminology resources of this type."""
    resources = [
        builder(await _load_disease_concepts(db))
        for rtype, _rid, builder in _PUBLISHED_VOCABULARIES
        if rtype == resource_type
    ]
    raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
    return build_search_bundle(
        base_url=base_url,
        path=f"/{resource_type}",
        query_string=raw_qs.encode("utf-8"),
        resources=resources,
        total=len(resources),
        offset=0,
        count=50,
    )


# Curried read/search fns bound to a resource type (ResourceEntry stores plain
# callables with the (db, ...) signature — these closures inject the type).


def make_read_fn(resource_type: str):
    async def _fn(db: AsyncSession, resource_id: str, current_user):
        return await read_terminology(
            db, resource_id, current_user, resource_type=resource_type
        )

    return _fn


def make_search_fn(resource_type: str):
    async def _fn(db: AsyncSession, query_params, current_user, base_url):
        return await search_terminology(
            db, query_params, current_user, base_url, resource_type=resource_type
        )

    return _fn


__all__ = [
    "make_read_fn",
    "make_search_fn",
    "read_terminology",
    "search_terminology",
]

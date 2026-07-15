import re
from pydantic import BaseModel, ConfigDict, field_validator
from uuid import UUID
from typing import Optional, List
from app.models.enums import CodingSystem

# Safe identifier for biomarker slugs. The slug is interpolated into raw SQL
# in the telemetry analytics path (see app/services/analytics_service.py), so
# it must be a strict identifier — no quotes, semicolons, spaces, or other
# characters that could break out of the interpolation context.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def is_safe_slug(value: str) -> bool:
    """Return True if ``value`` is safe to interpolate into SQL identifiers/literals."""
    return bool(value and _SLUG_RE.fullmatch(value))


def sanitize_slug(value: str) -> str:
    """Coerce arbitrary input into a SQL-safe slug (defence for direct model writes).

    Used by the AI pipeline / import paths that bypass the Pydantic ``slug``
    validator. Lowercases, replaces any non ``[a-z0-9_-]`` run with a single
    hyphen, trims leading/trailing hyphens, and truncates to 80 chars. Falls
    back to ``"biomarker"`` if the result is empty.
    """
    if not value:
        return "biomarker"
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-")
    cleaned = cleaned[:80].strip("-")
    return cleaned or "biomarker"


class UnitResponse(BaseModel):
    id: UUID
    symbol: str
    name: str
    quantity_type: str

    model_config = ConfigDict(from_attributes=True)


class UnitCreate(BaseModel):
    symbol: str
    name: str
    quantity_type: Optional[str] = "other"


class BiomarkerBase(BaseModel):
    slug: str
    coding_system: Optional[CodingSystem] = CodingSystem.LOINC
    code: Optional[str] = None
    name: str
    # Backward-compat: ``category`` is the readable string (the linked
    # ``biomarker_class`` concept's name). For writes prefer
    # ``class_concept_id``; ``category`` is best-effort resolved to a concept
    # in the biomarker endpoint / catalog import.
    category: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    # The class concept *slug* — the canonical key used by the backup
    # export/import path. ``category`` is the concept *name* and does not
    # round-trip through ``biomarker_category_to_concept_slug`` (which only
    # swaps ``_``→``-``), so without this slug the class link is silently
    # dropped on restore. CatalogImportService resolves this ahead of the
    # legacy ``category`` string when both are present.
    class_concept_slug: Optional[str] = None
    aliases: List[str] = []
    info: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    is_telemetry: Optional[bool] = False


class BiomarkerCreate(BiomarkerBase):
    preferred_unit_symbol: Optional[str] = None
    preferred_unit_id: Optional[UUID] = None

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        # The slug is interpolated into raw SQL in the telemetry analytics
        # path, so it must be a strict identifier. Reject anything outside
        # [A-Za-z0-9_-] to prevent second-order SQL injection. Validated on
        # the create (input) path only — the response schema must be able to
        # serialize rows that pre-date this guard or arrived via the pipeline.
        if not is_safe_slug(v):
            raise ValueError(
                "slug must be 1-80 chars of [A-Za-z0-9_-] only"
            )
        return v


class BiomarkerUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    aliases: Optional[List[str]] = None
    info: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    is_telemetry: Optional[bool] = None
    preferred_unit_id: Optional[UUID] = None


class BiomarkerRemapRequest(BaseModel):
    """Relink unmapped observations to a biomarker definition.

    Observations are matched by their stored code.text against ``source_name``
    (case-insensitive). Scope to a patient when ``patient_id`` is provided.
    """

    source_name: str
    patient_id: Optional[UUID] = None


class BiomarkerResponse(BiomarkerBase):
    id: UUID
    preferred_unit_id: Optional[UUID]
    preferred_unit_symbol: Optional[str] = None
    meta_data: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class CatalogMetadata(BaseModel):
    version: str
    source: str
    last_updated: str


class CatalogImportPayload(BaseModel):
    metadata: Optional[CatalogMetadata] = None
    units: List[UnitCreate] = []
    biomarkers: List[BiomarkerCreate] = []

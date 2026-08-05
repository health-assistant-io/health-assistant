import re
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from uuid import UUID
from typing import Optional, List
from app.models.enums import CodingSystem, Gender, BiomarkerValueType

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
    # Discriminator (plan state-biomarkers-2026-08-05). QUANTITY = numeric
    # value + unit + numeric reference ranges (the legacy default). STATE =
    # categorical value drawn from ``allowed_states`` (the normal set is
    # ``is_normal=True`` rows, replacing numeric ref ranges).
    value_type: BiomarkerValueType = BiomarkerValueType.QUANTITY
    # STATE biomarkers only: when True the biomarker accepts Observations
    # with FHIR ``component[]`` (one ``valueCodeableConcept`` per
    # sub-context) instead of a single top-level value. Ignored for QUANTITY.
    supports_multi_state: Optional[bool] = False

    @model_validator(mode="after")
    def _validate_value_type_invariants(self):
        """Cross-field invariants that the DB also enforces via CHECK
        constraints (defence-in-depth: reject bad payloads at the schema
        layer for a clean 422 instead of an opaque 500)."""
        if self.value_type == BiomarkerValueType.STATE:
            if self.is_telemetry:
                raise ValueError(
                    "STATE biomarkers cannot be telemetry "
                    "(telemetry_data.value is Float NOT NULL)"
                )
            # ``preferred_unit_id`` / ``preferred_unit_symbol`` are not on
            # ``BiomarkerBase`` (they live on Create/Response) — checked by
            # the biomarker endpoint and the create validator below.
        return self


class AllowedStateSpec(BaseModel):
    """Input shape: declare a STATE biomarker's accepted state by slug.

    The slug resolves to a ``BiomarkerState`` row at the endpoint layer; the
    slug is the stable round-trip key (catalog export/import, seed files).
    """

    state_slug: str
    is_normal: bool = False
    sort_order: int = 0


class BiomarkerStateResponse(BaseModel):
    """A row from the universal ``biomarker_states`` catalog."""

    id: UUID
    slug: str
    code: str
    system: str
    display: str
    description: Optional[str] = None
    category: Optional[str] = None
    sort_order: int = 0

    model_config = ConfigDict(from_attributes=True)


class BiomarkerAllowedStateResponse(BaseModel):
    """A STATE biomarker's resolved allowed-state entry (join row + state)."""

    state_id: UUID
    state_slug: str
    code: str
    system: str
    display: str
    is_normal: bool
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class BiomarkerCreate(BiomarkerBase):
    preferred_unit_symbol: Optional[str] = None
    preferred_unit_id: Optional[UUID] = None
    # Stratified reference ranges (audit B9/F3). Carried through the catalog
    # import/seed path so the default catalog can ship demographic-specific
    # ranges. Forward-ref resolved via model_rebuild() at module end.
    reference_ranges: List["BiomarkerReferenceRangeCreate"] = []
    # STATE biomarkers only: the states this biomarker accepts (and which are
    # in its normal set via ``is_normal``). Required non-empty for STATE.
    allowed_states: List[AllowedStateSpec] = []

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

    @model_validator(mode="after")
    def _validate_value_type_create_invariants(self):
        """Create-path invariants that depend on Create-only fields
        (preferred_unit_*, allowed_states)."""
        if self.value_type == BiomarkerValueType.STATE:
            if self.preferred_unit_id is not None or self.preferred_unit_symbol:
                raise ValueError(
                    "STATE biomarkers carry no unit (categorical values are unitless)"
                )
            if not self.allowed_states:
                raise ValueError(
                    "STATE biomarkers must declare at least one allowed_state"
                )
            if self.reference_range_min is not None or self.reference_range_max is not None:
                raise ValueError(
                    "STATE biomarkers use allowed_states (is_normal) — "
                    "numeric reference_range_min/max do not apply"
                )
        else:  # QUANTITY
            if self.allowed_states:
                raise ValueError(
                    "allowed_states / supports_multi_state apply to STATE biomarkers only"
                )
            if self.supports_multi_state:
                raise ValueError(
                    "supports_multi_state applies to STATE biomarkers only"
                )
        return self


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
    value_type: Optional[BiomarkerValueType] = None
    supports_multi_state: Optional[bool] = None
    allowed_states: Optional[List[AllowedStateSpec]] = None

    @model_validator(mode="after")
    def _validate_value_type_update_invariants(self):
        """Update-path invariants. ``value_type`` itself cannot be flipped
        here — that's a destructive operation requiring a dedicated migration
        of existing observations; reject it to fail loud rather than silently
        strand rows. The endpoint enforces the same rule for fields outside
        this schema (preferred_unit, reference_range_min/max)."""
        if self.value_type is not None:
            raise ValueError(
                "value_type cannot be changed via PATCH — drop and recreate the "
                "biomarker definition (observations would need re-mapping)"
            )
        if self.is_telemetry and self.supports_multi_state:
            raise ValueError(
                "STATE biomarkers (supports_multi_state=True) cannot be telemetry"
            )
        return self


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
    # Stratified reference ranges (audit B9/F3). Forward-ref resolved at the
    # bottom of the module via model_rebuild().
    reference_ranges: List["BiomarkerReferenceRangeResponse"] = []
    # STATE biomarkers only: resolved allowed-state set (the universal catalog
    # rows + per-biomarker is_normal / sort_order). Empty for QUANTITY.
    allowed_states: List[BiomarkerAllowedStateResponse] = []

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Stratified reference ranges (audit B9 / F3)
# ---------------------------------------------------------------------------


class BiomarkerReferenceRangeBase(BaseModel):
    """A reference range scoped to a sub-population (sex / age window / unit).

    A NULL dimension means "any value" for that axis (NULL ``sex`` → both
    sexes, NULL ``age_*`` → unbounded on that side, NULL ``unit_id`` → any
    unit). The resolver (``app.services.reference_ranges``) picks the
    most-specific applicable row for a patient.
    """

    sex: Optional[Gender] = None
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    unit_id: Optional[UUID] = None
    low: Optional[float] = None
    high: Optional[float] = None
    text: Optional[str] = None
    applies_to: Optional[str] = None

    @model_validator(mode="after")
    def _validate_range(self):
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("low must be <= high")
        if (
            self.age_min is not None
            and self.age_max is not None
            and self.age_min > self.age_max
        ):
            raise ValueError("age_min must be <= age_max")
        return self


class BiomarkerReferenceRangeCreate(BiomarkerReferenceRangeBase):
    pass


class BiomarkerReferenceRangeUpdate(BiomarkerReferenceRangeBase):
    # Every field optional on update; only supplied fields are applied.
    sex: Optional[Gender] = None
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    unit_id: Optional[UUID] = None
    low: Optional[float] = None
    high: Optional[float] = None
    text: Optional[str] = None
    applies_to: Optional[str] = None


class BiomarkerReferenceRangeResponse(BiomarkerReferenceRangeBase):
    id: UUID
    biomarker_id: UUID

    model_config = ConfigDict(from_attributes=True)


class CatalogMetadata(BaseModel):
    version: str
    source: str
    last_updated: str


class CatalogImportPayload(BaseModel):
    metadata: Optional[CatalogMetadata] = None
    units: List[UnitCreate] = []
    biomarkers: List[BiomarkerCreate] = []


# Resolve the forward reference on BiomarkerResponse.reference_ranges and
# BiomarkerCreate.reference_ranges.
BiomarkerResponse.model_rebuild()
BiomarkerCreate.model_rebuild()

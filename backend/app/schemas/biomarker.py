import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.enums import BiomarkerValueType, CodingSystem, Gender

# Safe identifier for biomarker slugs. The slug is interpolated into raw SQL
# in the telemetry analytics path (see app/services/analytics/trends.py), so
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
    id: UUID | None = None
    symbol: str
    name: str
    quantity_type: str | None = "other"


class BiomarkerBase(BaseModel):
    slug: str
    coding_system: CodingSystem | None = CodingSystem.LOINC
    code: str | None = None
    name: str
    description: str | None = None
    class_concept_id: UUID | None = None
    # The class concept *slug* — the canonical, round-tripping key for the
    # class link (backup export/import, seed files, the ontology-catalog URL
    # door). An explicit ``class_concept_id`` wins over it.
    class_concept_slug: str | None = None
    aliases: list[str] = []
    info: str | None = None
    reference_range_min: float | None = None
    reference_range_max: float | None = None
    is_telemetry: bool | None = False
    # Discriminator (plan state-biomarkers-2026-08-05). QUANTITY = numeric
    # value + unit + numeric reference ranges (the default). STATE =
    # categorical value drawn from ``allowed_states`` (the normal set is
    # ``is_normal=True`` rows, replacing numeric ref ranges).
    value_type: BiomarkerValueType = BiomarkerValueType.QUANTITY
    # STATE biomarkers only: when True the biomarker accepts Observations
    # with FHIR ``component[]`` (one ``valueCodeableConcept`` per
    # sub-context) instead of a single top-level value. Ignored for QUANTITY.
    supports_multi_state: bool | None = False

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_value_type_invariants(self):
        """Cross-field invariants that the DB also enforces via CHECK
        constraints (defence-in-depth: reject bad payloads at the schema
        layer for a clean 422 instead of an opaque 500)."""
        if self.value_type == BiomarkerValueType.STATE and self.is_telemetry:
            raise ValueError(
                "STATE biomarkers cannot be telemetry (telemetry_data.value is Float NOT NULL)"
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
    description: str | None = None
    category: str | None = None
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
    # Server-owned identity (plan D8): the slug may be omitted on the API
    # create path — the write service derives it from the name (same
    # deterministic algorithm as vaccine, per-scope unique via auto-suffix).
    # An explicit slug stays honored (seed/import payloads) and a duplicate
    # one still collides → 409. ``BiomarkerBase``/``BiomarkerResponse.slug``
    # stay required: every stored row (and every response) carries one.
    slug: str | None = None
    preferred_unit_symbol: str | None = None
    preferred_unit_id: UUID | None = None
    # Stratified reference ranges (audit B9/F3). Carried through the catalog
    # import/seed path so the default catalog can ship demographic-specific
    # ranges. Forward-ref resolved via model_rebuild() at module end.
    reference_ranges: list["BiomarkerReferenceRangeCreate"] = []
    # STATE biomarkers only: the states this biomarker accepts (and which are
    # in its normal set via ``is_normal``). Required non-empty for STATE.
    allowed_states: list[AllowedStateSpec] = []

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str | None) -> str | None:
        # The slug is interpolated into raw SQL in the telemetry analytics
        # path, so it must be a strict identifier. Reject anything outside
        # [A-Za-z0-9_-] to prevent second-order SQL injection. Validated on
        # the create (input) path only — the response schema must be able to
        # serialize rows that pre-date this guard or arrived via the pipeline.
        # An omitted slug is legal: the write service derives it from the
        # name (server-owned identity).
        if not v:
            return v
        if not is_safe_slug(v):
            raise ValueError("slug must be 1-80 chars of [A-Za-z0-9_-] only")
        return v

    @model_validator(mode="after")
    def _validate_value_type_create_invariants(self):
        """Create-path invariants that depend on Create-only fields
        (preferred_unit_*, allowed_states)."""
        if self.value_type == BiomarkerValueType.STATE:
            if self.preferred_unit_id is not None or self.preferred_unit_symbol:
                raise ValueError("STATE biomarkers carry no unit (categorical values are unitless)")
            if not self.allowed_states:
                raise ValueError("STATE biomarkers must declare at least one allowed_state")
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
                raise ValueError("supports_multi_state applies to STATE biomarkers only")
        return self


class BiomarkerUpdate(BaseModel):
    """Partial update (all fields optional; ``value_type`` is not changeable).

    Class-concept precedence (uniform across every catalog type): a sent
    ``class_concept_id`` wins over ``class_concept_slug`` — an explicit
    ``null`` id clears the link and any slug sent alongside is ignored; a
    bare slug re-resolves tenant-explicitly (unknown → 400).
    """

    coding_system: CodingSystem | None = None
    code: str | None = None
    name: str | None = None
    description: str | None = None
    class_concept_id: UUID | None = None
    class_concept_slug: str | None = None
    aliases: list[str] | None = None
    info: str | None = None
    reference_range_min: float | None = None
    reference_range_max: float | None = None
    is_telemetry: bool | None = None
    preferred_unit_id: UUID | None = None
    preferred_unit_symbol: str | None = None
    value_type: BiomarkerValueType | None = None
    supports_multi_state: bool | None = None
    allowed_states: list[AllowedStateSpec] | None = None

    model_config = ConfigDict(extra="forbid")

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
            raise ValueError("STATE biomarkers (supports_multi_state=True) cannot be telemetry")
        return self


class BiomarkerRemapRequest(BaseModel):
    """Relink unmapped observations to a biomarker definition.

    Observations are matched by their stored code.text against ``source_name``
    (case-insensitive). Scope to a patient when ``patient_id`` is provided.
    """

    source_name: str
    patient_id: UUID | None = None


class BiomarkerResponse(BiomarkerBase):
    id: UUID
    seed_key: str | None = None
    preferred_unit_id: UUID | None
    preferred_unit_symbol: str | None = None
    class_concept_name: str | None = None
    meta_data: dict | None = None
    scope: str | None = None
    tenant_id: UUID | None = None
    created_by: UUID | None = None
    # Stratified reference ranges (audit B9/F3). Forward-ref resolved at the
    # bottom of the module via model_rebuild().
    reference_ranges: list["BiomarkerReferenceRangeResponse"] = []
    # STATE biomarkers only: resolved allowed-state set (the universal catalog
    # rows + per-biomarker is_normal / sort_order). Empty for QUANTITY.
    allowed_states: list[BiomarkerAllowedStateResponse] = []

    model_config = ConfigDict(from_attributes=True, extra="ignore")


# ---------------------------------------------------------------------------
# Stratified reference ranges (audit B9 / F3)
# ---------------------------------------------------------------------------


class BiomarkerReferenceRangeBase(BaseModel):
    """A reference range scoped to a sub-population (sex / age window / unit).

    A NULL dimension means "any value" for that axis (NULL ``sex`` → both
    sexes, NULL ``age_*`` → unbounded on that side, NULL ``unit_id`` → any
    unit). The resolver (``app.services.biomarkers.reference_ranges``) picks the
    most-specific applicable row for a patient.
    """

    sex: Gender | None = None
    age_min: float | None = None
    age_max: float | None = None
    unit_id: UUID | None = None
    low: float | None = None
    high: float | None = None
    text: str | None = None
    applies_to: str | None = None

    @model_validator(mode="after")
    def _validate_range(self):
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("low must be <= high")
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("age_min must be <= age_max")
        return self


class BiomarkerReferenceRangeCreate(BiomarkerReferenceRangeBase):
    pass


class BiomarkerReferenceRangeUpdate(BiomarkerReferenceRangeBase):
    # Every field optional on update; only supplied fields are applied.
    sex: Gender | None = None
    age_min: float | None = None
    age_max: float | None = None
    unit_id: UUID | None = None
    low: float | None = None
    high: float | None = None
    text: str | None = None
    applies_to: str | None = None


class BiomarkerReferenceRangeResponse(BiomarkerReferenceRangeBase):
    id: UUID
    biomarker_id: UUID

    model_config = ConfigDict(from_attributes=True)


class CatalogMetadata(BaseModel):
    """The default-catalog envelope's descriptive metadata.

    ``last_updated`` is optional: the canonical seed-pack export (ADR 0056)
    carries no timestamps — a re-export of an unchanged DB is byte-stable —
    and the ingest path must accept that form unchanged.
    """

    version: str
    source: str
    last_updated: str | None = None


class CatalogImportPayload(BaseModel):
    metadata: CatalogMetadata | None = None
    units: list[UnitCreate] = []
    biomarkers: list[BiomarkerCreate] = []


# Resolve the forward reference on BiomarkerResponse.reference_ranges and
# BiomarkerCreate.reference_ranges.
BiomarkerResponse.model_rebuild()
BiomarkerCreate.model_rebuild()

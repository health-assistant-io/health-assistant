from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.schemas.fhir.observation import ObservationCreate
from app.models.enums import CodingSystem

# UCUM is the standard code system for clinical units; the builder assumes
# quantitative values are UCUM-coded. Surface as a constant so a caller can
# read/compare it even though there's no per-call override today.
UCUM_SYSTEM = "http://unitsofmeasure.org"

# Placeholder relative score when a reference range is present but incomplete
# (only one bound, or low >= high). Documented so the magic number has a name.
INCOMPLETE_RANGE_RELATIVE_SCORE = 0.5


class ObservationBuilder:
    """Fluent builder for :class:`~app.schemas.fhir.observation.ObservationCreate`.

    Stateful and **mutated in place** — every setter returns ``self``. To build
    multiple observations, call :meth:`reset` between them (or construct a fresh
    builder per observation) so conditionally-set fields don't leak from one
    record into the next. ``set_effective_date`` is **required** — a clinical
    observation with no timestamp is a data-correctness bug, so :meth:`build`
    raises if it was never set.
    """

    def __init__(self, tenant_id: UUID, patient_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.patient_id = patient_id
        self._status: str = "final"
        self._effective_datetime: Optional[datetime] = None
        self._code: Optional[str] = None
        self._coding_system: CodingSystem = CodingSystem.LOINC
        self._display_name: Optional[str] = None
        self._value: Optional[float] = None
        self._unit: Optional[str] = None
        self._unit_code: Optional[str] = None
        self._value_string: Optional[str] = None
        # valueCodeableConcept for STATE biomarkers (plan state-biomarkers
        # Step 9). Mutually exclusive with the numeric and string slots —
        # FHIR R4 §3.1.1 allows exactly one value[x].
        self._value_codeable_concept: Optional[Dict[str, Any]] = None
        self._biomarker_id: Optional[UUID] = None
        self._reference_range: Optional[Dict[str, float]] = None
        self._interpretation: Optional[str] = None
        # Multi-value / structural fields the inbound FHIR converter
        # (fhir_observation_to_create) already preserves but the builder
        # historically couldn't author. Used for blood pressure (component),
        # panel grouping (category), provenance (performer), and clinician
        # notes (comment).
        self._components: list[Dict[str, Any]] = []
        self._performer: Optional[List[Dict[str, Any]]] = None
        self._categories: list[Dict[str, Any]] = []
        self._comment: Optional[str] = None

    def reset(self) -> "ObservationBuilder":
        """Clear all fields except ``tenant_id`` / ``patient_id``.

        Useful when reusing one builder instance across loop iterations —
        avoids the state-leak footgun where a conditionally-set field
        (reference range, interpretation) from the previous record bleeds into
        the next. Returns ``self`` for chaining.
        """
        self._status = "final"
        self._effective_datetime = None
        self._code = None
        self._coding_system = CodingSystem.LOINC
        self._display_name = None
        self._value = None
        self._unit = None
        self._unit_code = None
        self._value_string = None
        self._value_codeable_concept = None
        self._biomarker_id = None
        self._reference_range = None
        self._interpretation = None
        self._components = []
        self._performer = None
        self._categories = []
        self._comment = None
        return self

    def set_status(self, status: str) -> "ObservationBuilder":
        """Set the FHIR Observation.status (default ``"final"``)."""
        self._status = status
        return self

    def set_effective_date(self, dt: datetime) -> "ObservationBuilder":
        """Set the clinically-correct time the reading was taken.

        **Required.** ``build()`` raises if this was never called — defaulting
        to "now" silently turned historical readings into current ones, which
        is a data-correctness bug in a clinical pipeline. Naive datetimes are
        assumed UTC; aware datetimes are kept as-is.
        """
        self._effective_datetime = dt
        return self

    def set_biomarker(
        self,
        code: str,
        display_name: str,
        coding_system: CodingSystem = CodingSystem.LOINC,
        biomarker_id: Optional[UUID] = None,
    ) -> "ObservationBuilder":
        """Set the observation code + coding system (LOINC/SNOMED/CUSTOM)."""
        self._code = code
        self._display_name = display_name
        self._coding_system = coding_system
        self._biomarker_id = biomarker_id
        return self

    def set_value(self, value: float, unit: str, unit_code: Optional[str] = None) -> "ObservationBuilder":
        """Set a quantitative value (FHIR ``valueQuantity``).

        Mutually exclusive with :meth:`set_value_string` and
        :meth:`set_value_codeable_concept` — FHIR R4 §3.1.1 allows exactly
        one ``value[x]``. Calling this clears the other slots; the last
        value-setter wins.
        """
        self._value = value
        self._unit = unit
        self._unit_code = unit_code
        self._value_string = None
        self._value_codeable_concept = None
        # A value[x] observation has no components (FHIR R4 §3.1.1).
        self._components = []
        return self

    def set_value_string(self, value: str) -> "ObservationBuilder":
        """Set a categorical / free-text value (FHIR ``valueString``).

        Mutually exclusive with :meth:`set_value` — FHIR R4 §3.1.1 allows
        exactly one ``value[x]`` per Observation. Calling this after
        :meth:`set_value` clears the quantitative slot; the reverse also
        holds. ``raw_value``/``normalized_value``/``relative_score`` are
        not meaningful for string values and are left unset.

        Prefer :meth:`set_value_codeable_concept` for coded categorical
        results (Positive/Negative/Detected/...) — those produce a proper
        ``valueCodeableConcept`` that the biomarker validator accepts for
        STATE biomarkers. Reserve ``valueString`` for genuinely free-text
        results with no controlled vocabulary.
        """
        self._value_string = value
        self._value = None
        self._unit = None
        self._unit_code = None
        self._value_codeable_concept = None
        # A value[x] observation has no components (FHIR R4 §3.1.1).
        self._components = []
        return self

    def set_value_codeable_concept(
        self,
        code: str,
        system: str,
        display: Optional[str] = None,
    ) -> "ObservationBuilder":
        """Set a coded categorical value (FHIR ``valueCodeableConcept``).

        This is the proper shape for STATE biomarkers (Positive / Negative /
        Detected / Susceptible / Within Limits / ...). The biomarker
        validator rejects ``value_string`` on STATE biomarkers — only
        ``valueCodeableConcept`` is accepted, and its ``coding[0].{code,
        system}`` pair must be in the biomarker's ``allowed_states`` set.

        Mutually exclusive with :meth:`set_value` and :meth:`set_value_string`
        — FHIR R4 §3.1.1 allows exactly one ``value[x]``.
        """
        coding: Dict[str, Any] = {"code": code, "system": system}
        if display:
            coding["display"] = display
        self._value_codeable_concept = {"coding": [coding]}
        # Clear the other value[x] slots — last setter wins.
        self._value = None
        self._unit = None
        self._unit_code = None
        self._value_string = None
        # A component observation has no parent value[x]; conversely a value[x]
        # observation has no components (FHIR R4 §3.1.1).
        self._components = []
        return self

    def add_component(
        self,
        code: str,
        display: str,
        value: float,
        unit: str,
        unit_code: Optional[str] = None,
        coding_system: CodingSystem = CodingSystem.LOINC,
    ) -> "ObservationBuilder":
        """Append a component (FHIR ``component[]``) for multi-value observations.

        Essential for blood pressure (systolic + diastolic under one panel
        code), lab panels, and any observation carrying multiple correlated
        measurements. Per FHIR R4 §3.1.1, an Observation with ``component[]``
        has **no parent** ``value[x]`` — calling this clears any previously-set
        value (``set_value`` / ``set_value_string`` /
        ``set_value_codeable_concept``), and any subsequent value-setter
        clears the components. The last mutation wins.
        """
        component: Dict[str, Any] = {
            "code": {
                "coding": [
                    {
                        "system": coding_system.fhir_system,
                        "code": code,
                        "display": display,
                    }
                ],
                "text": display,
            },
            "valueQuantity": {"value": value, "system": UCUM_SYSTEM},
        }
        if unit:
            component["valueQuantity"]["unit"] = unit
        if unit_code or unit:
            component["valueQuantity"]["code"] = unit_code or unit
        self._components.append(component)
        # Component observations have no parent value[x].
        self._value = None
        self._unit = None
        self._unit_code = None
        self._value_string = None
        self._value_codeable_concept = None
        return self

    def add_category(
        self,
        code: str,
        system: Optional[str] = None,
        display: Optional[str] = None,
    ) -> "ObservationBuilder":
        """Append a category (FHIR ``category[]``) — e.g. ``vital-signs``,
        ``laboratory``, ``imaging``, ``social-history``.

        Multiple categories may be attached (a blood-pressure observation is
        both ``vital-signs`` and may belong to a clinic-specific group).
        """
        coding: Dict[str, Any] = {"code": code}
        if system:
            coding["system"] = system
        if display:
            coding["display"] = display
        self._categories.append({"coding": [coding]})
        return self

    def set_performer(
        self,
        display: str,
        reference: Optional[str] = None,
        performer_type: Optional[str] = None,
    ) -> "ObservationBuilder":
        """Set the performer (who/what produced the result).

        ``reference`` is a FHIR reference like ``"Organization/<uuid>"`` or
        ``"Integration/<id>"``; ``performer_type`` is the resource type
        (``"Organization"``, ``"Practitioner"``, ``"Integration"``).
        Replaces any previously-set performer (an observation's performer list
        is small enough that single-set semantics is the common case; append
        manually via the ``performer`` field on the built model if needed).
        """
        entry: Dict[str, Any] = {"display": display}
        if reference:
            entry["reference"] = reference
        if performer_type:
            entry["type"] = performer_type
        self._performer = [entry]
        return self

    def add_note(self, text: str) -> "ObservationBuilder":
        """Append a free-text comment (FHIR ``note``).

        Multiple notes are joined with ``" "`` at build time into the
        ``comment`` field on ``ObservationCreate`` (the storage schema keeps a
        single comment column). For the rare case where you need the full
        ``note[]`` array, set it on the built model directly.
        """
        if not text:
            return self
        self._comment = f"{self._comment} {text}".strip() if self._comment else text
        return self

    def set_reference_range(
        self, low: Optional[float] = None, high: Optional[float] = None
    ) -> "ObservationBuilder":
        """Set the reference range (FHIR ``referenceRange``).

        Either or both bounds may be supplied. Calling with both ``None``
        clears any previously-set range (so a record without a range following
        one with a range doesn't inherit the stale range when the builder is
        reused).
        """
        if low is None and high is None:
            self._reference_range = None
            return self
        self._reference_range = {}
        if low is not None:
            self._reference_range["low"] = low
        if high is not None:
            self._reference_range["high"] = high
        return self

    def set_interpretation(self, interpretation: str) -> "ObservationBuilder":
        """Set the FHIR interpretation code (e.g. ``"normal"`` / ``"high"``)."""
        self._interpretation = interpretation
        return self

    def build(self) -> ObservationCreate:
        if not self._code or not self._display_name:
            raise ValueError("Biomarker code and display name are required")
        if self._effective_datetime is None:
            raise ValueError(
                "effective_datetime is required — a clinical observation with "
                "no timestamp is a data-correctness bug. Call set_effective_date()."
            )

        # Map enum to proper FHIR system URL
        system_url = self._coding_system.fhir_system

        coding = [
            {
                "system": system_url,
                "code": self._code,
                "display": self._display_name
            }
        ]

        unit = self._unit or None
        unit_code = self._unit_code or unit
        value_quantity: Optional[dict] = None
        if self._value is not None:
            value_quantity = {"value": self._value}
            if unit:
                value_quantity["unit"] = unit
            value_quantity["system"] = UCUM_SYSTEM
            if unit_code:
                value_quantity["code"] = unit_code

        # Calculate a mock relative score if reference range is present.
        # Only meaningful for quantitative values — categoricals get None.
        relative_score = None
        if self._reference_range and self._value is not None:
            low = self._reference_range.get("low")
            high = self._reference_range.get("high")
            if low is not None and high is not None and high > low:
                relative_score = (self._value - low) / (high - low)
                relative_score = max(0.0, min(1.0, relative_score))
            else:
                relative_score = INCOMPLETE_RANGE_RELATIVE_SCORE

        # Keep timezone-aware datetimes. asyncpg handles TIMESTAMP WITH TIME
        # ZONE columns natively for tz-aware Python datetimes; stripping tzinfo
        # would make isoformat() fail the FHIR R4 regex and cause every
        # SDK-built observation to be silently dropped by assert_valid_fhir.
        # If a caller passes a naive datetime, assume UTC.
        eff_dt = self._effective_datetime
        if eff_dt.tzinfo is None:
            eff_dt = eff_dt.replace(tzinfo=timezone.utc)

        # FHIR R4 §3.1.1: an Observation has exactly one value[x]. Emit
        # whichever slot was set: value_codeable_concept (STATE biomarkers) →
        # value_string (free-text categoricals) → value_quantity (numeric).
        value_string = self._value_string if self._value_string is not None else None
        value_codeable_concept = self._value_codeable_concept or None

        # For STATE / string observations the numeric raw_value/normalized_value
        # are not meaningful — leave them None so analytics never tries to
        # chart a string on a numeric axis.
        raw_value = self._value if self._value is not None else None
        normalized_value = self._value if self._value is not None else None

        return ObservationCreate(
            tenant_id=self.tenant_id,
            subject={"reference": f"Patient/{self.patient_id}"},
            status=self._status,
            code={
                "coding": coding,
                "text": self._display_name
            },
            effective_datetime=eff_dt,
            value_quantity=value_quantity,
            value_string=value_string,
            value_codeable_concept=value_codeable_concept,
            raw_value=raw_value,
            normalized_value=normalized_value,
            biomarker_id=self._biomarker_id,
            lab_reference_range=self._reference_range,
            relative_score=relative_score,
            interpretation=self._interpretation,
            component=list(self._components) if self._components else None,
            performer=list(self._performer) if self._performer else None,
            category=list(self._categories) if self._categories else None,
            comment=self._comment,
        )

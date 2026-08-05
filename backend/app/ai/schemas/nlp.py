from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator
from app.models.enums import CodingSystem


class MetricMappingRequest(BaseModel):
    name: str
    code: Optional[str] = None


class MappedMetric(BaseModel):
    original_name: str
    action: Literal["map_to_existing", "create_new"]
    existing_biomarker_id: Optional[str] = Field(
        None, description="UUID of the existing biomarker if mapped"
    )
    new_biomarker_name: Optional[str] = Field(
        None, description="Standardized English name if creating new"
    )
    new_biomarker_code: Optional[str] = Field(
        None, description="LOINC code if available, otherwise short custom code"
    )
    new_biomarker_coding_system: Optional[str] = Field(
        "loinc", description="'loinc' or 'custom'"
    )


class MapResponsePayload(BaseModel):
    mappings: List[MappedMetric]


class KnownBiomarkerExtract(BaseModel):
    name: str = Field(description="Exact text from document")
    matched_slug: str = Field(
        description="The slug from the provided catalog that matches this biomarker"
    )
    # Numeric path (the legacy default). Optional now — STATE biomarkers
    # populate ``value_state_*`` instead. The validator below enforces that
    # exactly one of (value, value_state_code) is set so the OCR pipeline
    # never emits a half-formed numeric-or-state result.
    value: Optional[float] = Field(
        None, description="Numeric value (omit for state/qualitative results)"
    )
    unit_symbol: Optional[str] = Field(
        None, description="e.g. mg/dL, mmol/L (omit for state/qualitative results)"
    )
    # State path (plan state-biomarkers Step 8). Populated only when the
    # matched biomarker is value_type=STATE. ``value_state_code`` must be one
    # of the biomarker's allowed-state codes (the LLM is given the allowed
    # list in the prompt; ``value_state_system`` disambiguates same-code-
    # different-system cases). Persistence builds valueCodeableConcept from
    # this pair and skips the numeric pipeline entirely.
    value_state_code: Optional[str] = Field(
        None,
        description="State code (e.g. POS/NEG/IND) when the biomarker is "
        "value_type=state. Must be drawn from the allowed_states list given "
        "in the prompt.",
    )
    value_state_system: Optional[str] = Field(
        None,
        description="The code system URL for value_state_code "
        "(e.g. http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation).",
    )
    value_state_display: Optional[str] = Field(
        None,
        description="Human-readable label for the state (e.g. 'Positive').",
    )
    method: Optional[str] = Field(None, description="e.g. Calculated, Direct Assay")
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    interpretation_flag: Optional[str] = Field(
        None, description="e.g. High, Low, Normal, H, L"
    )

    @model_validator(mode="after")
    def _exactly_one_value_path(self):
        """A biomarker result is either numeric (``value`` set) or state
        (``value_state_code`` set), never both, never neither."""
        has_numeric = self.value is not None
        has_state = self.value_state_code is not None
        if has_numeric and has_state:
            raise ValueError(
                "KnownBiomarkerExtract: set either value (numeric) or "
                "value_state_code (state), not both"
            )
        if not has_numeric and not has_state:
            raise ValueError(
                "KnownBiomarkerExtract: must set value (numeric) or "
                "value_state_code (state)"
            )
        # A numeric value without a unit is allowed (some biomarkers are
        # unitless ratios); a state value with a unit is contradictory.
        if has_state and self.unit_symbol:
            raise ValueError(
                "KnownBiomarkerExtract: unit_symbol must be empty when "
                "value_state_code is set"
            )
        return self


class UnknownBiomarkerExtract(BaseModel):
    raw_name: str = Field(
        description="Exact name from document for the unknown biomarker"
    )
    value: float
    unit_symbol: str = Field(description="e.g. mg/dL, mmol/L")
    method: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    interpretation_flag: Optional[str] = None


class PatientInfoExtract(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None


class KnownMedicationExtract(BaseModel):
    name: str = Field(description="Exact text from document")
    matched_catalog_id: Optional[str] = Field(
        None,
        description="The ID from the provided catalog that matches this medication",
    )
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    status: str = "ACTIVE"
    reason: Optional[str] = None


class UnknownMedicationExtract(BaseModel):
    raw_name: str = Field(description="Exact name from document")
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    status: str = "ACTIVE"
    reason: Optional[str] = None


class DocumentEntitiesExtract(BaseModel):
    document_category: str = Field(description="General category of the document")
    patient_info: PatientInfoExtract
    known_biomarkers: List[KnownBiomarkerExtract]
    unknown_biomarkers: List[UnknownBiomarkerExtract]
    known_medications: List[KnownMedicationExtract]
    unknown_medications: List[UnknownMedicationExtract]
    diagnoses: List[str]
    impressions: str = Field(description="General impressions or findings")


class NewBiomarkerDefinition(BaseModel):
    raw_name_match: str = Field(
        description="The exact raw_name from the input that this definition is for"
    )
    proposed_slug: str = Field(
        description="A URL-friendly, lowercase string, e.g., new-biomarker"
    )
    proposed_coding_system: CodingSystem = Field(
        default=CodingSystem.CUSTOM,
        description="The medical coding system to map to (e.g., 'loinc', 'snomed', 'custom'). Try to map standard lab tests to 'loinc'.",
    )
    proposed_code: Optional[str] = Field(
        None,
        description="The specific code from the proposed_coding_system (e.g., the LOINC code like '2345-7'). If 'custom', provide a short identifier.",
    )
    name: str = Field(description="Clean, standard name of the biomarker")
    category: str = Field(description="e.g. blood_laboratory, vital_signs, imaging")
    suggested_aliases: List[str] = Field(
        description="List of alternative names or abbreviations"
    )
    reference_range_min: Optional[float] = Field(
        None,
        description="The minimum value of the normal reference range. If not in input, provide standard clinical value if known.",
    )
    reference_range_max: Optional[float] = Field(
        None,
        description="The maximum value of the normal reference range. If not in input, provide standard clinical value if known.",
    )
    preferred_unit_symbol: Optional[str] = Field(
        None,
        description="The standard unit symbol for this biomarker (e.g., mg/dL, mmol/L). ALWAYS provide this.",
    )
    info: Optional[str] = Field(
        None,
        description="Detailed patient-friendly information about the biomarker in Markdown format. Explain what it is, why it's important, and how it affects the patient's health.",
    )
    is_telemetry: bool = Field(
        False,
        description="Set to true if this metric is typically tracked continuously via IoT/wearables (e.g., heart rate, steps, continuous glucose).",
    )


class NewBiomarkerDefinitions(BaseModel):
    definitions: List[NewBiomarkerDefinition]


class NewMedicationDefinition(BaseModel):
    raw_name_match: str = Field(
        description="The exact name from the input that this definition is for"
    )
    name: str
    description: Optional[str] = None
    indications: Optional[str] = None
    side_effects: List[str] = Field(default_factory=list)
    contraindications: Optional[str] = None
    dosage_info: Optional[str] = None


class NewMedicationDefinitions(BaseModel):
    definitions: List[NewMedicationDefinition]


class ExaminationMetadataExtract(BaseModel):
    examination_date: Optional[str] = Field(
        None,
        description="The date the examination occurred (ISO format, e.g. 2024-03-21)",
    )
    doctor_names: List[str] = Field(
        default_factory=list, description="List of doctor names found in the document"
    )
    category: Optional[str] = Field(
        None,
        description="Pick EXACTLY one clinical category SLUG from the provided list. Do not combine categories. If it is a new specialty, suggest a compact kebab-case slug (e.g., 'dermatology').",
    )
    clinical_notes: Optional[str] = Field(
        None, description="Summary of clinical findings and notes from the doctor"
    )

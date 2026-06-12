from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.models.enums import CodingSystem


class KnownBiomarkerExtract(BaseModel):
    name: str = Field(description="Exact text from document")
    matched_slug: str = Field(
        description="The slug from the provided catalog that matches this biomarker"
    )
    value: float
    unit_symbol: str = Field(description="e.g. mg/dL, mmol/L")
    method: Optional[str] = Field(None, description="e.g. Calculated, Direct Assay")
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    interpretation_flag: Optional[str] = Field(
        None, description="e.g. High, Low, Normal, H, L"
    )


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
        description="The medical coding system to map to (e.g., 'loinc', 'snomed', 'custom'). Try to map standard lab tests to 'loinc'."
    )
    proposed_code: Optional[str] = Field(
        None,
        description="The specific code from the proposed_coding_system (e.g., the LOINC code like '2345-7'). If 'custom', provide a short identifier."
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

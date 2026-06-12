import json
from typing import Dict, Any, List, Optional
import logging

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel

from .base import NLPExtractor
from app.schemas.ai_nlp import (
    DocumentEntitiesExtract,
    NewBiomarkerDefinitions,
    NewMedicationDefinitions,
    UnknownBiomarkerExtract,
    UnknownMedicationExtract,
    ExaminationMetadataExtract,
)

logger = logging.getLogger(__name__)


class LangChainStructuredExtractor(NLPExtractor):
    """
    A structured NLP extractor that uses LangChain's `with_structured_output`.
    Works with any BaseChatModel that supports structured extraction (OpenAI, Anthropic, etc.).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        llm: Optional[BaseChatModel] = None,
    ):
        if llm:
            self.llm = llm
        else:
            self.llm = ChatOpenAI(
                api_key=api_key,
                base_url=api_base,
                model=model,
                temperature=temperature,
            )

    async def extract_entities(self, text: str) -> Dict[str, Any]:
        """Base implementation - should not be used in the new pipeline directly without catalog"""
        return await self.parse_document_pass_1(text, [], [])

    async def parse_document_pass_1(
        self,
        text: str,
        biomarker_catalog: List[Dict[str, Any]],
        medication_catalog: List[Dict[str, Any]],
        reference_data: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> DocumentEntitiesExtract:
        """First pass: Extract data and map to known catalogs where possible"""
        biomarker_catalog_str = json.dumps(biomarker_catalog, indent=2)
        medication_catalog_str = json.dumps(medication_catalog, indent=2)

        reference_context = ""
        if reference_data:
            reference_context = f"\n\nHere is the PREVIOUS context for this examination. Please merge new findings into this:\n{json.dumps(reference_data, indent=2)}\n"

        system_prompt = """You are a medical data extraction assistant.
You will be provided with the raw text of a medical document (like lab results or clinical notes).
Your task is to extract all medical entities perfectly structured.

Here is the current catalog of KNOWN biomarkers:
{biomarker_catalog}

Here is the current catalog of KNOWN medications:
{medication_catalog}
{reference_context}
Instructions:
1. Try to match each extracted biomarker to the `slug` of a known biomarker.
2. Try to match each extracted medication to the `id` of a known medication.
3. If it matches, add to `known_*` list with the correct identifier.
4. If it absolutely does not match, add it to `unknown_*` list.
5. Parse numerical values strictly as floats, including result `value`, `reference_range_min`, and `reference_range_max`.
6. For medications:
   - Extract the CLEAN, GENERIC name (e.g., 'Phenylephrine Hydrochloride' instead of 'Phenylephrine Hydrochloride 2.5% Ophthalmic Solution').
   - DO NOT include dosages, concentrations, or delivery formats in the name.
   - Extract dosage, frequency, and reason if available into their respective fields.
7. Provide a concise narrative `impressions` summary.
"""

        logger.info(
            f"Calling LLM for structured extraction (Pass 1) using model {getattr(self.llm, 'model_name', 'default')}"
        )
        try:
            structured_llm = self.llm.with_structured_output(DocumentEntitiesExtract)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", "{text}"),
                ]
            )
            chain = prompt | structured_llm
            return await chain.ainvoke(
                {
                    "text": text,
                    "biomarker_catalog": biomarker_catalog_str,
                    "medication_catalog": medication_catalog_str,
                    "reference_context": reference_context,
                }
            )
        except Exception as e:
            logger.error(f"Pass 1 Extraction failed: {e}")
            raise

    async def parse_document_pass_2_biomarkers(
        self, unknown_biomarkers: List[UnknownBiomarkerExtract], timeout: float = 45.0
    ) -> NewBiomarkerDefinitions:
        """Second pass: Generate standard definitions for unknown biomarkers"""

        system_prompt = """You are a medical ontology expert.
You will be provided with a list of raw, unknown biomarkers extracted from a patient document.
Your task is to generate a standardized definition for each one so they can be added to the global medical catalog.

IMPORTANT RULES:
1. For each definition, you must include the `raw_name_match` which is the exact `raw_name` from the input.
2. Ensure the `proposed_slug` is universally standard (lowercase, kebab-case).
3. Determine the `proposed_coding_system` (e.g. 'loinc', 'snomed', or 'custom'). If it is a common laboratory test, try to use 'loinc'.
4. If the coding system is known, provide the exact `proposed_code` (e.g. '2345-7' for LOINC glucose). If custom, make a brief ID.
5. Categorize appropriately (e.g., blood_laboratory, vital_signs, urine).
6. Suggest common abbreviations or aliases.
7. If the input data contains `reference_range_min` or `reference_range_max`, YOU MUST use those values as the definition's reference ranges.
8. Provide a `preferred_unit_symbol` (e.g. mg/dL, mmol/L) that is the standard for this biomarker in clinical practice.
9. If those values are NOT in the input, but you know the standard clinical ranges for this biomarker, you may provide them.
10. ALL values must be returned as floats.
11. FOR EACH BIOMARKER, you MUST generate the `info` field in Markdown format. If you don't have specific clinical knowledge for a rare biomarker, provide the best possible general medical explanation or mark the section as 'Consult your physician for details'.
   Structure the `info` as follows:
   ### What is it?
   A simple explanation for a patient.
   ### Why is it measured?
   The clinical importance.
   ### How it affects you?
   Relationship to the patient's symptoms or overall health.
   ### High Levels Mean
   What to look out for if it's elevated.
   ### Low Levels Mean
   What to look out for if it's decreased.
   ### Other Information
   Any additional useful clinical context.
"""

        biomarker_data = [
            {
                "raw_name": b.raw_name,
                "unit": b.unit_symbol,
                "reference_range_min": b.reference_range_min,
                "reference_range_max": b.reference_range_max,
            }
            for b in unknown_biomarkers
        ]

        logger.info(
            f"Calling LLM to generate definitions for {len(unknown_biomarkers)} new biomarkers"
        )
        try:
            structured_llm = self.llm.with_structured_output(NewBiomarkerDefinitions)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", "{biomarker_data}"),
                ]
            )
            chain = prompt | structured_llm
            return await chain.ainvoke({"biomarker_data": json.dumps(biomarker_data)})
        except Exception as e:
            logger.error(f"Pass 2 Biomarker Definition generation failed: {e}")
            raise

    async def parse_document_pass_2_medications(
        self, unknown_medications: List[UnknownMedicationExtract], timeout: float = 45.0
    ) -> NewMedicationDefinitions:
        """Second pass: Generate standard definitions for unknown medications"""

        system_prompt = """You are a pharmaceutical ontology expert.
You will be provided with a list of raw medications extracted from a patient document.
Your task is to generate a standardized catalog definition for each one.
IMPORTANT: 
1. For each definition, you must include the `raw_name_match` which is the exact `name` from the input.
2. The `name` field must be the CLEAN, GENERIC name of the medication (e.g., 'Phenylephrine Hydrochloride' instead of 'Phenylephrine Hydrochloride 2.5% Ophthalmic Solution'). 
3. DO NOT include dosages, concentrations, or delivery formats (like 'Ophthalmic Solution', 'Tablets', '2.5%') in the `name` field.
4. Include a brief description, common indications, side effects, and contraindications.
"""

        med_data = [{"name": m.raw_name} for m in unknown_medications]

        logger.info(
            f"Calling LLM to generate definitions for {len(unknown_medications)} new medications"
        )
        try:
            structured_llm = self.llm.with_structured_output(NewMedicationDefinitions)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", "{med_data}"),
                ]
            )
            chain = prompt | structured_llm
            return await chain.ainvoke({"med_data": json.dumps(med_data)})
        except Exception as e:
            logger.error(f"Pass 2 Medication Definition generation failed: {e}")
            raise

    async def parse_examination_metadata(
        self,
        text: str,
        known_categories: Optional[List[str]] = None,
        timeout: float = 45.0,
    ) -> ExaminationMetadataExtract:
        """Extract examination date, doctors, category, and notes from text"""

        categories_context = ""
        if known_categories:
            categories_context = f"\n\nHere is the list of existing clinical category SLUGS. Pick EXACTLY one if it matches:\n{', '.join(known_categories)}\n"

        system_prompt = f"""You are a medical administrative assistant.
Your task is to extract high-level examination details from the provided medical document text.
{categories_context}
RULES FOR CATEGORY:
1. Compare the document content against the list of known category SLUGS provided.
2. If a known category slug is a strong match, you MUST use it exactly.
3. If the document represents a distinct clinical specialty not in the list, you may suggest a new SLUG.
4. New categories must be a single, compact kebab-case slug (e.g., 'dermatology').
5. Do NOT concatenate multiple categories. Pick the primary one.

Extract:
1. `examination_date`: The date the visit or test occurred. If multiple dates exist, use the primary visit date. Format: YYYY-MM-DD.
2. `doctor_names`: List of attending or referring physicians. IMPORTANT: Provide ONLY the name, without titles like 'Dr.', 'Doctor', 'Prof.', 'MD', etc.
3. `category`: The best fit clinical category SLUG.
4. `clinical_notes`: A concise summary of the visit reason and findings if mentioned in the document header/notes.

If a field is not found, leave it as null or an empty list.
"""

        logger.info("Calling LLM for examination metadata extraction")
        try:
            structured_llm = self.llm.with_structured_output(ExaminationMetadataExtract)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("human", "{text}"),
                ]
            )
            chain = prompt | structured_llm
            return await chain.ainvoke({"text": text})
        except Exception as e:
            logger.error(f"Examination metadata extraction failed: {e}")
            raise

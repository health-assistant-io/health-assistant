from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from app.schemas.ai_nlp import (
    DocumentEntitiesExtract,
    NewBiomarkerDefinitions,
    NewMedicationDefinitions,
    ExaminationMetadataExtract,
)


class NLPExtractor(ABC):
    """Base class for NLP extractors"""

    @abstractmethod
    async def extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract medical entities from text"""
        pass

    async def parse_document_pass_1(
        self,
        text: str,
        biomarker_catalog: List[Dict[str, Any]],
        medication_catalog: List[Dict[str, Any]],
        reference_data: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> DocumentEntitiesExtract:
        raise NotImplementedError()

    async def parse_document_pass_2_biomarkers(
        self, unknown_biomarkers: List[Any], timeout: float = 45.0
    ) -> NewBiomarkerDefinitions:
        raise NotImplementedError()

    async def parse_document_pass_2_medications(
        self, unknown_medications: List[Any], timeout: float = 45.0
    ) -> NewMedicationDefinitions:
        raise NotImplementedError()

    async def parse_examination_metadata(
        self,
        text: str,
        known_categories: Optional[List[str]] = None,
        timeout: float = 45.0,
    ) -> ExaminationMetadataExtract:
        raise NotImplementedError()

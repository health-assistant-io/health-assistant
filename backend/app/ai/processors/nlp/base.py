# ruff: noqa: E501 -- long immutable strings; reflow when touched
from abc import ABC, abstractmethod
from typing import Any

from app.ai.schemas.nlp import (
    DocumentEntitiesExtract,
    ExaminationMetadataExtract,
    MapResponsePayload,
    MetricMappingRequest,
    NewBiomarkerDefinitions,
    NewMedicationDefinitions,
)


class NLPExtractor(ABC):
    """Base class for NLP extractors"""

    @abstractmethod
    async def extract_entities(self, text: str) -> dict[str, Any]:
        """Extract medical entities from text"""
        pass

    async def map_external_metrics(
        self,
        raw_metrics: list[MetricMappingRequest],
        existing_catalog_str: str,
        timeout: float = 45.0,
    ) -> MapResponsePayload:
        """Map third party integration metric names to the local standardized catalog."""
        raise NotImplementedError(
            "The currently configured NLP provider does not support AI ontology mapping. Please assign an LLM provider to the 'nlp' task in the AI Settings."
        )

    async def parse_document_pass_1(
        self,
        text: str,
        biomarker_catalog: list[dict[str, Any]],
        medication_catalog: list[dict[str, Any]],
        reference_data: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> DocumentEntitiesExtract:
        raise NotImplementedError()

    async def parse_document_pass_2_biomarkers(
        self, unknown_biomarkers: list[Any], timeout: float = 45.0
    ) -> NewBiomarkerDefinitions:
        raise NotImplementedError()

    async def parse_document_pass_2_medications(
        self, unknown_medications: list[Any], timeout: float = 45.0
    ) -> NewMedicationDefinitions:
        raise NotImplementedError()

    async def parse_examination_metadata(
        self,
        text: str,
        known_categories: list[str] | None = None,
        timeout: float = 45.0,
    ) -> ExaminationMetadataExtract:
        raise NotImplementedError()

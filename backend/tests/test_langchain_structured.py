import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.processors.nlp.langchain_structured import LangChainStructuredExtractor
from app.schemas.ai_nlp import (
    DocumentEntitiesExtract,
    NewBiomarkerDefinitions,
    NewMedicationDefinitions,
)


@pytest.fixture
def mock_llm():
    # Patch the ChatOpenAI in the NEW file location
    with patch("app.processors.nlp.langchain_structured.ChatOpenAI") as mock_chat:
        mock_llm_instance = MagicMock()
        mock_chat.return_value = mock_llm_instance
        yield mock_llm_instance


@pytest.mark.asyncio
async def test_parse_document_pass_1(mock_llm):
    extractor = LangChainStructuredExtractor(api_key="test-key")

    # Mock the response from LLM
    mock_parsed_response = MagicMock(spec=DocumentEntitiesExtract)
    mock_parsed_response.document_category = "Laboratory Tests"
    mock_parsed_response.patient_info = MagicMock(name="John Doe")
    mock_parsed_response.known_biomarkers = []
    mock_parsed_response.unknown_biomarkers = []
    mock_parsed_response.known_medications = []
    mock_parsed_response.unknown_medications = []
    mock_parsed_response.diagnoses = []
    mock_parsed_response.impressions = ""

    # Mock the chain execution
    mock_ainvoke = AsyncMock(return_value=mock_parsed_response)
    mock_llm.with_structured_output.return_value = mock_ainvoke

    result = await extractor.parse_document_pass_1(
        "Test doc",
        [{"slug": "glucose", "name": "Glucose"}],
        [{"id": "aspirin", "name": "Aspirin"}],
    )

    assert result == mock_parsed_response


@pytest.mark.asyncio
async def test_parse_document_pass_2_biomarkers(mock_llm):
    extractor = LangChainStructuredExtractor(api_key="test-key")

    # Mock the response from LLM
    mock_parsed_response = MagicMock(spec=NewBiomarkerDefinitions)
    mock_parsed_response.definitions = []

    # Mock the chain execution
    mock_ainvoke = AsyncMock(return_value=mock_parsed_response)
    mock_llm.with_structured_output.return_value = mock_ainvoke

    # Mock an unknown biomarker
    unknown_bio = MagicMock()
    unknown_bio.raw_name = "New Test"
    unknown_bio.unit_symbol = "mg/L"
    unknown_bio.reference_range_min = None
    unknown_bio.reference_range_max = None

    result = await extractor.parse_document_pass_2_biomarkers([unknown_bio])

    assert result == mock_parsed_response


@pytest.mark.asyncio
async def test_parse_document_pass_2_medications(mock_llm):
    extractor = LangChainStructuredExtractor(api_key="test-key")

    # Mock the response from LLM
    mock_parsed_response = MagicMock(spec=NewMedicationDefinitions)
    mock_parsed_response.definitions = []

    # Mock the chain execution
    mock_ainvoke = AsyncMock(return_value=mock_parsed_response)
    mock_llm.with_structured_output.return_value = mock_ainvoke

    # Mock an unknown medication
    unknown_med = MagicMock()
    unknown_med.raw_name = "New Med"

    result = await extractor.parse_document_pass_2_medications([unknown_med])

    assert result == mock_parsed_response

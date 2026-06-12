import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from app.processors.ocr.langchain_vision import LangChainOCRProcessor
from langchain_core.messages import AIMessage


@pytest.fixture
def mock_langchain_processor():
    return LangChainOCRProcessor(
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        model="gpt-4-vision-preview",
    )


@pytest.mark.asyncio
async def test_langchain_extract_structured_data_clean_json(
    mock_langchain_processor, tmp_path
):
    # Create a dummy file
    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy content")

    schema = {"document_category": "string", "medications": ["string"]}

    parsed_json = {
        "document_category": "Ophthalmology",
        "medications": ["Lisinopril 10mg"],
    }

    # Mock LangChain's ainvoke by patching the class method
    with patch(
        "langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock
    ) as mock_ainvoke:
        mock_ainvoke.return_value = AIMessage(content=json.dumps(parsed_json))

        result = await mock_langchain_processor.extract_structured_data(
            test_file, schema
        )

        assert result["document_category"] == "Ophthalmology"
        assert len(result["medications"]) == 1
        assert result["medications"][0] == "Lisinopril 10mg"


@pytest.mark.asyncio
async def test_langchain_extract_structured_data_pure_json(
    mock_langchain_processor, tmp_path
):
    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy content")
    schema = {"document_category": "string"}

    parsed_json = {"document_category": "Cardiology"}

    with patch(
        "langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock
    ) as mock_ainvoke:
        mock_ainvoke.return_value = AIMessage(content=json.dumps(parsed_json))

        result = await mock_langchain_processor.extract_structured_data(
            test_file, schema
        )
        assert result["document_category"] == "Cardiology"

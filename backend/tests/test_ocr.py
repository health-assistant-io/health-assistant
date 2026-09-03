import json

import pytest
from app.ai.processors.ocr.langchain_vision import LangChainOCRProcessor
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


def _make_processor(content: str) -> LangChainOCRProcessor:
    """Build the processor with an injected fake chat model (ADR-0008).

    ``GenericFakeChatModel`` is a real ``BaseChatModel``, so the processor's
    ``llm | parser`` chain runs the genuine JsonOutputParser over the canned
    response — same shape as the previous patched-``ainvoke`` tests.
    """
    llm = GenericFakeChatModel(messages=iter([AIMessage(content=content)]))
    return LangChainOCRProcessor(llm=llm)


@pytest.mark.asyncio
async def test_langchain_extract_structured_data_clean_json(tmp_path):
    # Create a dummy file
    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy content")

    schema = {"document_category": "string", "medications": ["string"]}

    parsed_json = {
        "document_category": "Ophthalmology",
        "medications": ["Lisinopril 10mg"],
    }

    processor = _make_processor(json.dumps(parsed_json))

    result = await processor.extract_structured_data(test_file, schema)

    assert result["document_category"] == "Ophthalmology"
    assert len(result["medications"]) == 1
    assert result["medications"][0] == "Lisinopril 10mg"


@pytest.mark.asyncio
async def test_langchain_extract_structured_data_pure_json(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("dummy content")
    schema = {"document_category": "string"}

    parsed_json = {"document_category": "Cardiology"}

    processor = _make_processor(json.dumps(parsed_json))

    result = await processor.extract_structured_data(test_file, schema)
    assert result["document_category"] == "Cardiology"

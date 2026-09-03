from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .base import OCRProcessor
from .langchain_vision import LangChainOCRProcessor
from .tesseract import TesseractOCRProcessor
from app.ai import chat_models
from langchain_core.language_models.chat_models import BaseChatModel


def get_ocr_processor(
    provider: str = "openai",
    api_key: str = None,
    api_base: str = None,
    model: str = None,
    max_tokens: int = 65536,
    temperature: float = 0.0,
    llm: Optional[BaseChatModel] = None,
    **kwargs,
) -> OCRProcessor:
    """Factory function to get OCR processor based on configuration.

    The LLM-backed processor is injected with its chat model: an explicitly
    provided ``llm`` wins; otherwise one is built through the canonical
    model factory (``app.ai.chat_models``) from the remaining config —
    LangChain chat classes are never constructed outside the factory
    (ADR-0008).
    """
    if provider == "openai":
        if llm is None:
            llm = chat_models.build_openai(
                api_key=api_key,
                base_url=api_base or "https://api.openai.com/v1",
                model_name=model or "gpt-4-vision-preview",
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return LangChainOCRProcessor(llm=llm)
    elif provider == "tesseract":
        return TesseractOCRProcessor(language=kwargs.get("language", "eng"))
    else:
        raise ValueError(
            f"Unsupported OCR provider: {provider}. Only 'openai' and 'tesseract' are supported."
        )


async def get_ocr_processor_from_db(
    db: AsyncSession, task_type: str = "ocr", tenant_id: Optional[UUID] = None
) -> OCRProcessor:
    """Get OCR processor configured from database"""
    from app.ai.providers.service import AIProviderService

    service = AIProviderService(db)
    return await service.get_ocr_processor(tenant_id)


__all__ = [
    "OCRProcessor",
    "LangChainOCRProcessor",
    "TesseractOCRProcessor",
    "get_ocr_processor",
    "get_ocr_processor_from_db",
]

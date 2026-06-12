from typing import Optional, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .base import OCRProcessor
from .langchain_vision import LangChainOCRProcessor
from .tesseract import TesseractOCRProcessor
from app.models.ai_provider_model import AIProviderModel, AIModel
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
    """Factory function to get OCR processor based on configuration"""
    if provider == "openai":
        return LangChainOCRProcessor(
            api_key=api_key,
            api_base=api_base or "https://api.openai.com/v1",
            model=model or "gpt-4-vision-preview",
            max_tokens=max_tokens,
            temperature=temperature,
            llm=llm,
        )
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
    from app.services.ai_provider_service import AIProviderService

    service = AIProviderService(db)
    return await service.get_ocr_processor(tenant_id)


__all__ = [
    "OCRProcessor",
    "LangChainOCRProcessor",
    "TesseractOCRProcessor",
    "get_ocr_processor",
    "get_ocr_processor_from_db",
]

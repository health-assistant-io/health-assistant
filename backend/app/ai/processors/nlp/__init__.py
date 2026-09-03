from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .base import NLPExtractor
from .spacy_extractor import SpaCyExtractor
from .langchain_structured import LangChainStructuredExtractor
from app.ai import chat_models
from langchain_core.language_models.chat_models import BaseChatModel


def get_nlp_extractor(
    provider: str = "spacy",
    api_key: str = None,
    api_base: str = None,
    model: str = None,
    max_tokens: int = 65536,
    temperature: float = 0.7,
    llm: Optional[BaseChatModel] = None,
    **kwargs,
) -> NLPExtractor:
    """Factory function to get NLP extractor.

    The LLM-backed extractor is injected with its chat model: an explicitly
    provided ``llm`` wins; otherwise one is built through the canonical
    model factory (``app.ai.chat_models``) from the remaining config —
    LangChain chat classes are never constructed outside the factory
    (ADR-0008).
    """
    if provider == "spacy":
        return SpaCyExtractor(model=kwargs.get("model", "en_core_sci_sm"))
    elif provider == "openai":
        if llm is None:
            llm = chat_models.build_openai(
                api_key=api_key,
                base_url=api_base or "https://api.openai.com/v1",
                model_name=model or "gpt-4o-mini",
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return LangChainStructuredExtractor(llm=llm)
    else:
        raise ValueError(
            f"Unsupported NLP provider: {provider}. Only 'spacy' and 'openai' are supported."
        )


async def get_nlp_extractor_from_db(
    db: AsyncSession, task_type: str = "nlp", tenant_id: Optional[UUID] = None
) -> NLPExtractor:
    """Get NLP extractor configured from database"""
    from app.ai.providers.service import AIProviderService

    service = AIProviderService(db)
    return await service.get_nlp_extractor(tenant_id)


__all__ = [
    "NLPExtractor",
    "SpaCyExtractor",
    "LangChainStructuredExtractor",
    "get_nlp_extractor",
    "get_nlp_extractor_from_db",
]

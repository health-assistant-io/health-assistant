"""Wire-parameter routing of the ``build_openai`` factory (ADR-0038).

Modern OpenAI models reject ``max_tokens`` ("use max_completion_tokens
instead") while OpenAI-compatible endpoints (LocalAI, vLLM, Ollama, LM
Studio, llama.cpp) reject the newer name. The factory routes by endpoint —
never by model id, which prefixes, chat variants, and future families make
unreliable.
"""

from langchain_openai import ChatOpenAI

from app.ai.chat_models import (
    CompatibleChatOpenAI,
    build_openai,
    is_openai_endpoint,
)


def _build(base_url: str):
    return build_openai(
        api_key="sk-test",
        base_url=base_url,
        model_name="gpt-5-chat-latest",
        temperature=0.7,
        max_tokens=512,
    )


def test_is_openai_endpoint() -> None:
    assert is_openai_endpoint("https://api.openai.com/v1")
    assert not is_openai_endpoint("http://localhost:1234/v1")
    assert not is_openai_endpoint("")
    assert not is_openai_endpoint(None)


def test_openai_endpoint_cap_uses_modern_parameter() -> None:
    model = _build("https://api.openai.com/v1")
    assert isinstance(model, ChatOpenAI)
    assert not isinstance(model, CompatibleChatOpenAI)
    assert model._default_params["max_completion_tokens"] == 512
    assert "max_tokens" not in model._default_params


def test_compatible_endpoint_cap_keeps_classic_parameter() -> None:
    model = _build("http://localhost:1234/v1")
    assert isinstance(model, CompatibleChatOpenAI)
    # Still a ChatOpenAI — the registry isinstance checks keep passing.
    assert isinstance(model, ChatOpenAI)
    assert model._default_params["max_tokens"] == 512
    assert "max_completion_tokens" not in model._default_params

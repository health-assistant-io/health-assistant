"""Gateway embeddings path (§15 embeddings wave): ``app.ai.assistance.embeddings``.

Infrastructure-first module — these tests ARE its first consumer, which is
exactly how the STT branch landed (execution path + tests before a product
feature calls it). All network traffic rides httpx ``MockTransport`` — no
test ever leaves the process.
"""

import json

import httpx
import pytest

from app.ai.assistance.embeddings import EmbeddingError, run_embed
from app.ai.providers.errors import ProviderErrorCode


class _Provider:
    """Minimal provider double (the rows ``run_embed`` reads)."""

    def __init__(
        self,
        api_key="sk-embed-key",
        api_base="https://api.openai.com/v1",
        preset_key=None,
        is_local=False,
    ):
        self._api_key = api_key
        self.api_base = api_base
        self.preset_key = preset_key
        self.is_local = is_local

    def get_api_key_plaintext(self):
        return self._api_key


class _Model:
    def __init__(self, model_name, caps=("embeddings",)):
        self.model_name = model_name
        self._caps = list(caps)

    def get_capabilities(self):
        return list(self._caps)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_openai_compatible_happy_path_posts_model_and_input() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 1, "embedding": [0.3, 0.4]},
                ]
            },
        )

    vectors = await run_embed(
        ["hello", "world"],
        _Provider(),
        _Model("text-embedding-3-small"),
        transport=_transport(handler),
    )

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["url"] == "https://api.openai.com/v1/embeddings"
    assert seen["auth"] == "Bearer sk-embed-key"
    assert seen["body"] == {"model": "text-embedding-3-small", "input": ["hello", "world"]}


async def test_openai_compatible_respects_provider_index_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [9.0]},
                    {"index": 0, "embedding": [7.0]},
                ]
            },
        )

    vectors = await run_embed(
        ["a", "b"], _Provider(), _Model("mistral-embed"), transport=_transport(handler)
    )
    assert vectors == [[7.0], [9.0]]


async def test_401_classifies_invalid_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(EmbeddingError) as err:
        await run_embed(
            ["x"], _Provider(), _Model("text-embedding-3-small"), transport=_transport(handler)
        )
    assert err.value.classified is not None
    assert err.value.classified.code is ProviderErrorCode.INVALID_KEY


async def test_timeout_classifies_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(EmbeddingError) as err:
        await run_embed(
            ["x"], _Provider(), _Model("text-embedding-3-small"), transport=_transport(handler)
        )
    assert err.value.classified is not None
    assert err.value.classified.code is ProviderErrorCode.TIMEOUT


async def test_google_wire_uses_embed_content_with_key_param() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [{"values": [1.0, 2.0]}, {"values": [3.0]}]})

    vectors = await run_embed(
        ["hello", "again"],
        _Provider(api_key="AIza-test", api_base="https://generativelanguage.googleapis.com"),
        _Model("gemini-embedding-2"),
        wire_type="google",
        transport=_transport(handler),
    )

    assert vectors == [[1.0, 2.0], [3.0]]
    # Mirrors health's existing google call pattern (the setup catalog
    # fetcher): resource path appended to the base, key as a query param.
    assert seen["url"].split("?")[0] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-embedding-2:batchEmbedContents"
    )
    assert "key=AIza-test" in seen["url"]
    assert seen["body"] == {
        "requests": [
            {"content": {"parts": [{"text": "hello"}]}},
            {"content": {"parts": [{"text": "again"}]}},
        ]
    }


async def test_capability_guard_rejects_a_chat_only_model_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("network call attempted")

    with pytest.raises(EmbeddingError, match="embeddings' capability"):
        await run_embed(
            ["x"],
            _Provider(),
            _Model("gpt-5.6-terra", caps=["text"]),
            transport=_transport(handler),
        )


async def test_empty_input_makes_no_network_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("network call attempted")

    assert await run_embed([], _Provider(), _Model("text-embedding-3-small")) == []

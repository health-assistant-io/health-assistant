"""Unwired provider types are refused at write time.

Anthropic / Azure OpenAI / Bedrock / Ollama have reserved ``ProviderType``
members whose builders raise ``NotImplementedError`` on the first call. The
write paths used to persist such rows (and their models), advertising
configuration that could never run; they now return the API's typed machine
error (422 ``provider_type_unwired``) and wired types are unaffected.
"""

from uuid import uuid4

import pytest

UNWIRED = ("anthropic", "azure_openai", "bedrock", "ollama")
WIRED = ("openai", "tesseract")


async def _create_provider(client, headers, provider_type: str, name: str | None = None):
    return await client.post(
        "/api/v1/ai-config/providers",
        json={
            "name": name or f"provider-{provider_type}-{uuid4().hex[:8]}",
            "provider_type": provider_type,
            "api_base": "https://api.openai.com/v1",
            "api_key": "test-key",
            "is_active": True,
        },
        headers=headers,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", UNWIRED)
async def test_create_provider_refuses_unwired_type(
    async_client, system_admin_headers, provider_type
):
    """POST /providers rejects every reserved-and-unwired provider type."""
    response = await _create_provider(async_client, system_admin_headers, provider_type)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "provider_type_unwired"
    assert provider_type in detail["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", WIRED)
async def test_create_provider_accepts_wired_types(
    async_client, system_admin_headers, provider_type
):
    """Wired LLM / processor provider types keep working."""
    response = await _create_provider(async_client, system_admin_headers, provider_type)
    assert response.status_code == 201, response.text
    provider_id = response.json()["id"]
    deleted = await async_client.delete(
        f"/api/v1/ai-config/providers/{provider_id}", headers=system_admin_headers
    )
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_update_provider_refuses_unwired_type(async_client, system_admin_headers):
    """PUT /providers/{id} cannot switch a wired row to a reserved type."""
    created = await _create_provider(async_client, system_admin_headers, "openai")
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    try:
        for provider_type in UNWIRED:
            response = await async_client.put(
                f"/api/v1/ai-config/providers/{provider_id}",
                json={"provider_type": provider_type},
                headers=system_admin_headers,
            )
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "provider_type_unwired"

        still_openai = await async_client.get(
            f"/api/v1/ai-config/providers/{provider_id}", headers=system_admin_headers
        )
        assert still_openai.json()["provider_type"] == "openai"

        renamed = await async_client.put(
            f"/api/v1/ai-config/providers/{provider_id}",
            json={"name": "still writable"},
            headers=system_admin_headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "still writable"
    finally:
        await async_client.delete(
            f"/api/v1/ai-config/providers/{provider_id}", headers=system_admin_headers
        )


async def _seed_unwired_provider(provider_type: str = "anthropic"):
    """Insert reserved-type rows directly (bypassing the write guard)."""
    from app.core.database import AsyncSessionLocal
    from app.models.ai_provider_model import AIModel, AIProviderModel, AIScope

    async with AsyncSessionLocal() as session:
        provider = AIProviderModel(
            name=f"seeded-{provider_type}-{uuid4().hex[:8]}",
            scope=AIScope.SYSTEM,
            provider_type=provider_type,
            api_base="https://api.anthropic.com/v1",
            api_key=None,
            is_active=True,
            is_local=False,
            settings={},
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        model = AIModel(
            provider_id=provider.id,
            name="seeded model",
            model_name="claude-sonnet",
            capabilities=["text", "vision"],
            is_active=True,
            max_tokens=4096,
            settings={},
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
        return provider.id, model.id


@pytest.mark.asyncio
async def test_model_write_refused_under_unwired_provider(async_client, system_admin_headers):
    """Models cannot be created/updated under a reserved-type provider row."""
    provider_id, model_id = await _seed_unwired_provider()
    try:
        created = await async_client.post(
            f"/api/v1/ai-config/providers/{provider_id}/models",
            json={
                "provider_id": str(provider_id),
                "name": "new model",
                "model_name": "claude-haiku",
            },
            headers=system_admin_headers,
        )
        assert created.status_code == 422, created.text
        assert created.json()["detail"]["code"] == "provider_type_unwired"

        updated = await async_client.put(
            f"/api/v1/ai-config/models/{model_id}",
            json={"max_tokens": 2048},
            headers=system_admin_headers,
        )
        assert updated.status_code == 422, updated.text
        assert updated.json()["detail"]["code"] == "provider_type_unwired"
    finally:
        from sqlalchemy import delete

        from app.core.database import AsyncSessionLocal
        from app.models.ai_provider_model import AIProviderModel

        async with AsyncSessionLocal() as session:
            await session.execute(delete(AIProviderModel).where(AIProviderModel.id == provider_id))
            await session.commit()


@pytest.mark.asyncio
async def test_model_write_allowed_under_wired_provider(async_client, system_admin_headers):
    """A model under a wired provider is created and updated normally."""
    created = await _create_provider(async_client, system_admin_headers, "openai")
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    try:
        model = await async_client.post(
            f"/api/v1/ai-config/providers/{provider_id}/models",
            json={
                "provider_id": provider_id,
                "name": "gpt-4o",
                "model_name": "gpt-4o",
            },
            headers=system_admin_headers,
        )
        assert model.status_code == 201, model.text
        model_id = model.json()["id"]

        updated = await async_client.put(
            f"/api/v1/ai-config/models/{model_id}",
            json={"max_tokens": 2048},
            headers=system_admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["max_tokens"] == 2048
    finally:
        await async_client.delete(
            f"/api/v1/ai-config/providers/{provider_id}", headers=system_admin_headers
        )


def test_unwired_set_matches_the_stub_builders():
    """The write-time refusal set and the stub builders cannot drift."""
    from app.ai import chat_models
    from app.ai.providers.enums import ProviderType
    from app.ai.providers.registry import UNWIRED_PROVIDER_TYPES, get_llm_builder

    for provider_type in UNWIRED_PROVIDER_TYPES:
        with pytest.raises(NotImplementedError):
            get_llm_builder(provider_type.value)(
                api_key="k",
                base_url="https://example.com/v1",
                model_name="m",
                temperature=0.0,
                max_tokens=16,
            )

    assert {pt.value for pt in UNWIRED_PROVIDER_TYPES} == set(UNWIRED)
    for wired in (ProviderType.OPENAI, ProviderType.MOCK):
        assert wired not in UNWIRED_PROVIDER_TYPES
    assert chat_models.build_openai is get_llm_builder("openai")

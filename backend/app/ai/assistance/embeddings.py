"""Text embeddings against the assigned provider (the ``embed`` task).

INFRASTRUCTURE-FIRST: this module has NO product-feature caller yet — that
is intentional. It lands with the §15 embeddings wave so the ``embed``
task slot (bound by setup's ``bind_embeddings`` gap-fill) has a real,
tested execution path from day one, exactly like the ``transcription``
branch had before its first consumer. Features that need embedding
vectors (semantic search &c.) call :func:`run_embed`; nothing else in the
app does today.

Two wire shapes are implemented, mirroring the §15 preset wire vocabulary
(the same dispatch :func:`app.ai.providers.setup.fetch_remote_models`
uses for catalog fetches):

* ``openai_compatible`` (health's ``openai`` provider family — OpenAI,
  OpenRouter, Groq, Mistral, DeepSeek, Ollama): ``POST {base}/embeddings``
  with ``{model, input}`` → ``data[].embedding``;
* ``google`` (the preset wire type; health's registry has no wired google
  LLM builder yet, so this branch is reachable only via preset-stamped
  rows / mock transports — kept family-uniform like the catalog fetcher):
  ``POST {base}/v1beta/models/{model}:embedContent`` (single) or
  ``:batchEmbedContents`` (batch), key as a query param per health's
  existing google call pattern.

Errors are typed (:class:`EmbeddingError`) and carry the §15 classifier's
verdict (``invalid_key`` / ``timeout`` / …) so a future caller routes them
through the same i18n error enum as the setup/audio surfaces.

Security / privacy
------------------
Input text may contain PHI. It is sent to the assigned provider and the
vectors come back — nothing is persisted here, nothing is logged at
payload level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.providers.capabilities import required_capabilities_for_task
from app.ai.providers.enums import TaskType
from app.ai.providers.errors import ClassifiedProviderError, classify_provider_error
from app.ai.providers.presets import ALL_PRESETS
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmbedTarget:
    """Resolved embed provider/model to call + the auth/base/wire shape."""

    api_key: str | None
    api_base: str
    model_name: str
    wire_type: str


class EmbeddingError(RuntimeError):
    """Raised when the embed task is unavailable or the provider call fails.

    Subclasses ``RuntimeError`` (not ``ValueError``) so it is NOT mistaken
    for a soft guard message by endpoint error classifiers. ``classified``
    carries the §15 verdict when the failure came from the network call.
    """

    def __init__(
        self,
        message: str,
        classified: ClassifiedProviderError | None = None,
    ) -> None:
        super().__init__(message)
        self.classified = classified


def _wire_type_for(provider: Any) -> str:
    """The §15 wire vocabulary for the embed call.

    Preset-stamped rows resolve through the synced canonical data (a
    ``typesafe``-stamped row would speak its preset's wire shape); every
    other provider speaks the OpenAI-compatible shape — health's wired
    registry family is OpenAI-compatible end to end. (The google wire
    shape is exercised via the explicit ``wire_type`` override, the same
    way the setup catalog fetcher receives its wire type from the preset.)
    """
    preset_key = getattr(provider, "preset_key", None)
    if preset_key:
        preset = ALL_PRESETS.get(str(preset_key))
        if preset is not None:
            return str(preset["wire_type"])
    return "openai_compatible"


def resolve_embed_target(provider: Any, model: Any) -> EmbedTarget:
    """Build the embed call target from a resolved provider+model.

    Validates the resolved model advertises the ``embeddings`` capability —
    a misconfigured assignment to a chat-only model is rejected early with
    a clear message instead of a cryptic provider 400.
    """
    api_key = provider.get_api_key_plaintext() if provider else None
    api_base = provider.api_base if provider and provider.api_base else "https://api.openai.com/v1"
    model_name = model.model_name if model else ""

    required = required_capabilities_for_task(TaskType.EMBED.value)
    caps = model.get_capabilities() if model and hasattr(model, "get_capabilities") else None
    have = {str(c) for c in caps} if caps else set()
    if required and not any(c.value in have for c in required):
        raise EmbeddingError(
            f"Configured embeddings model '{model_name or '<none>'}' does not advertise the "
            f"'embeddings' capability. Assign an embeddings model "
            f"(e.g. text-embedding-3-small) to the embed task."
        )
    if not model_name:
        raise EmbeddingError("No model assigned to the embed task.")
    if not api_key and not getattr(provider, "is_local", False):
        raise EmbeddingError(
            "No API key configured for embeddings. Assign an embeddings provider with a key."
        )

    return EmbedTarget(
        api_key=api_key,
        api_base=(api_base or "").rstrip("/"),
        model_name=model_name,
        wire_type=_wire_type_for(provider),
    )


def _google_parts(texts: list[str]) -> dict[str, Any]:
    """The google request body: one ``embedContent`` request per text."""
    return {
        "requests": [{"content": {"parts": [{"text": text}]}} for text in texts],
    }


async def _run_embed_openai_compatible(
    client: httpx.AsyncClient, target: EmbedTarget, texts: list[str]
) -> list[list[float]]:
    response = await client.post(
        f"{target.api_base}/embeddings",
        headers={"Authorization": f"Bearer {target.api_key}"} if target.api_key else {},
        json={"model": target.model_name, "input": texts},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise EmbeddingError("Embeddings provider returned no vectors.")
    # Tolerate providers that shuffle order — the index field is canonical.
    ordered = sorted(data, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)
    vectors: list[list[float]] = []
    for item in ordered:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list):
            raise EmbeddingError("Embeddings provider returned a malformed vector.")
        vectors.append([float(v) for v in embedding])
    return vectors


async def _run_embed_google(
    client: httpx.AsyncClient, target: EmbedTarget, texts: list[str]
) -> list[list[float]]:
    # Health's existing google call pattern (setup's catalog fetcher): the
    # key rides the query string, the model resource sits under /v1beta.
    params = {"key": target.api_key} if target.api_key else None
    response = await client.post(
        f"{target.api_base}/v1beta/models/{target.model_name}:batchEmbedContents",
        params=params,
        json=_google_parts(texts),
    )
    response.raise_for_status()
    payload = response.json()
    embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise EmbeddingError("Embeddings provider returned no vectors.")
    vectors: list[list[float]] = []
    for entry in embeddings:
        values = entry.get("values") if isinstance(entry, dict) else None
        if not isinstance(values, list):
            raise EmbeddingError("Embeddings provider returned a malformed vector.")
        vectors.append([float(v) for v in values])
    return vectors


async def run_embed(
    texts: list[str],
    provider: Any,
    model: Any,
    *,
    wire_type: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[list[float]]:
    """Embed ``texts`` via the resolved ``embed`` assignment.

    Infrastructure-first (see the module docstring): no product-feature
    caller yet — the slot + this path exist so the first embeddings feature
    needs no new AI-layer plumbing. ``wire_type`` overrides the §15 wire
    dispatch (preset stamp → openai_compatible default), mirroring how the
    setup catalog fetcher receives its wire type. Raises
    :class:`EmbeddingError` on any failure; network failures carry the §15
    classified verdict (401 → ``invalid_key``, timeouts → ``timeout``, …)
    in ``classified``.
    """
    if not texts:
        return []
    target = resolve_embed_target(provider, model)
    if wire_type:
        target.wire_type = wire_type

    try:
        async with httpx.AsyncClient(
            timeout=settings.AI_EMBED_TIMEOUT_SECONDS, transport=transport
        ) as client:
            if target.wire_type == "google":
                return await _run_embed_google(client, target, texts)
            return await _run_embed_openai_compatible(client, target, texts)
    except EmbeddingError:
        raise
    except httpx.TimeoutException as exc:
        classified = classify_provider_error(
            exc,
            local_provider=bool(getattr(provider, "is_local", False)),
            api_key=target.api_key,
            attempted_preset=getattr(provider, "preset_key", None),
        )
        raise EmbeddingError("Embeddings request timed out.", classified) from exc
    except httpx.HTTPError as exc:
        classified = classify_provider_error(
            exc,
            local_provider=bool(getattr(provider, "is_local", False)),
            api_key=target.api_key,
            attempted_preset=getattr(provider, "preset_key", None),
        )
        logger.warning(
            "Embeddings provider call failed (wire=%s model=%s): %s",
            target.wire_type,
            target.model_name,
            exc,
        )
        raise EmbeddingError("Embeddings request failed.", classified) from exc

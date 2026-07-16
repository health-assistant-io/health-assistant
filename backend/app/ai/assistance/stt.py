"""Speech-to-text transcription against an OpenAI-compatible API.

STT is a different endpoint from chat completions (``POST /audio/transcriptions``,
multipart form), so the LangChain ``ChatOpenAI`` chat factory cannot serve it.
This module is the thin client that resolves the ``transcription`` task
assignment (a model advertising the ``audio_input`` capability) and POSTs the
compressed audio to the provider, returning plain text.

Security / privacy
------------------
Audio may contain PHI. It is transcribed and then **discarded** — never written
to the DB, never logged at payload level. The resulting text flows through the
existing chat pipeline (prompt guard + HITL wall) once it becomes ``user_input``.

Performance
-----------
Callers should send Opus/WebM at ~16 kHz mono (~24 kbps) — already tiny. The
``AI_STT_MAX_AUDIO_BYTES`` guard rejects anything oversized before the network
call. The whole operation is batch (one round-trip), not streamed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx

from app.ai.providers.capabilities import required_capabilities_for_task
from app.ai.providers.enums import TaskType
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class STTTarget:
    """Resolved STT provider/model to call + the auth/base URL."""

    api_key: Optional[str]
    api_base: str
    model_name: str


class TranscriptionError(RuntimeError):
    """Raised when transcription is unavailable or the provider call fails.

    Subclasses ``RuntimeError`` (not ``ValueError``) so it is NOT mistaken for
    a soft guard message by the endpoint error classifier — it surfaces as a
    generic streaming/HTTP error with no SDK text leak.
    """


def _resolve_stt_target(provider, model) -> STTTarget:
    """Build the STT call target from a resolved provider+model (or env fallback).

    Validates the resolved model advertises the ``audio_input`` capability —
    a misconfigured assignment to a chat-only model is rejected early with a
    clear message instead of a cryptic provider 400.
    """
    api_key = provider.get_api_key_plaintext() if provider else None
    api_base = (
        provider.api_base if provider and provider.api_base else "https://api.openai.com/v1"
    )
    model_name = model.model_name if model else settings.OPENAI_STT_MODEL

    required = required_capabilities_for_task(TaskType.TRANSCRIPTION.value)
    caps = model.get_capabilities() if model and hasattr(model, "get_capabilities") else None
    have = {str(c) for c in caps} if caps else set()
    if required and not any(c.value in have for c in required):
        raise TranscriptionError(
            f"Configured STT model '{model_name}' does not advertise the "
            f"'audio_input' capability. Assign a speech-to-text model "
            f"(e.g. whisper-1) to the transcription task."
        )

    if not api_key:
        raise TranscriptionError(
            "No API key configured for speech-to-text. Set an OPENAI_API_KEY "
            "or assign a transcription provider with a key."
        )

    return STTTarget(api_key=api_key, api_base=api_base.rstrip("/"), model_name=model_name)


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    mime_type: str,
    target: STTTarget,
) -> str:
    """POST compressed audio to ``{api_base}/audio/transcriptions`` and return
    the transcribed text.

    The audio is sent as multipart form data (``file`` + ``model``) per the
    OpenAI-compatible API contract. Raises :class:`TranscriptionError` on any
    failure (timeout, auth, non-2xx). The caller never sees raw provider text
    beyond the transcribed payload.
    """
    base = target.api_base.rstrip("/")
    url = f"{base}/audio/transcriptions"

    # httpx builds the multipart boundary itself; do NOT pre-set Content-Type.
    files = {"file": (filename, audio_bytes, mime_type)}
    data = {"model": target.model_name}

    try:
        async with httpx.AsyncClient(timeout=settings.AI_STT_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {target.api_key}"},
                files=files,
                data=data,
            )
    except httpx.TimeoutException as exc:
        raise TranscriptionError("Speech-to-text request timed out.") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError("Speech-to-text service is unreachable.") from exc

    if resp.status_code >= 400:
        logger.warning(
            "STT provider returned HTTP %s for model=%s", resp.status_code, target.model_name
        )
        if resp.status_code in (401, 403):
            raise TranscriptionError("Speech-to-text authentication failed.")
        if resp.status_code == 429:
            raise TranscriptionError("Speech-to-text rate limit reached. Try again shortly.")
        raise TranscriptionError(
            f"Speech-to-text failed (HTTP {resp.status_code})."
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise TranscriptionError("Speech-to-text returned a malformed response.") from exc

    # OpenAI returns {"text": "..."}; tolerate alternate shapes.
    text = payload.get("text") if isinstance(payload, dict) else None
    if not text and isinstance(payload, dict):
        # Some providers nest under "result" or return a bare string.
        text = payload.get("result") or payload.get("transcript")
    if not text:
        raise TranscriptionError("Speech-to-text returned no text.")
    return str(text).strip()


def split_filename(filename: str) -> Tuple[str, str]:
    """Return ``(stem, ext)`` for a filename, lowercased extension with dot."""
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        return stem, f".{ext.lower()}"
    return filename, ""

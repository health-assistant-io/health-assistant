from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import StreamingResponse
import json
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from uuid import UUID
from datetime import datetime, timezone

from app.core.database import get_db
from app.ai.assistance.service import AIAssistanceService
from app.ai.assistance.stt import TranscriptionError, transcribe_audio
from app.services.chat_session_service import ChatSessionService
from app.models.enums import HitlTaskStatus
from app.ai.schemas.assistance import (
    AIAssistanceRequest,
    AIAssistanceResponse,
    ChatSessionSchema,
    ChatMessageSchema,
    HitlResolutionRequest,
    HitlResumeRequest,
    AIAssistanceToolSchema,
)
from app.core.config import settings
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.utils.prompt_guard import check_user_input_safety

router = APIRouter(prefix="/ai-assistance", tags=["AI Assistance"])


# Audio MIME types accepted for STT — Opus/WebM (Chrome default) + the common
# fallbacks. WAV/PCM is allowed (some browsers lack Opus) but discouraged by the
# client, which always requests Opus when available.
_ALLOWED_AUDIO_MIME = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
}


# ---------------------------------------------------------------------------
# Streaming error classification
# ---------------------------------------------------------------------------
# The chat stream endpoints catch exceptions and forward them to the client as
# an SSE ``{"error": ..., "error_type": ...}`` payload. We never forward raw
# provider SDK strings (e.g. OpenAI's "Connection error.") because they leak
# the upstream vendor and aren't localized. Instead we classify the exception
# into a short, stable code that the frontend maps to a translated message.
# ValueError is the channel for *intentional* client-facing guard violations
# (ownership/no-tasks/pending-task checks); its message is already user-facing
# and is forwarded verbatim with error_type="guard".

_STREAM_ERROR_TYPES = (
    "connection",
    "auth",
    "rate_limit",
    "timeout",
    "generic",
    "guard",
)


def _classify_stream_error(exc: Exception) -> tuple[str, str]:
    """Return ``(error_type, user_message)`` for a streaming exception.

    The ``error_type`` is a stable lowercase code the frontend localizes.
    ``user_message`` is non-empty only for soft guard messages (ValueError);
    for provider errors it is empty so the frontend MUST localize from the
    code (no raw SDK text leaves the server).
    """
    if isinstance(exc, ValueError):
        # Intentional guard violation — message is already user-facing.
        return ("guard", str(exc))

    # Map known LLM/provider errors to non-leaky codes.
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        return ("generic", "")

    if isinstance(exc, APITimeoutError):
        return ("timeout", "")
    if isinstance(exc, APIConnectionError):
        return ("connection", "")
    if isinstance(exc, AuthenticationError):
        return ("auth", "")
    if isinstance(exc, RateLimitError):
        return ("rate_limit", "")
    return ("generic", "")


@router.post("/assist", response_model=AIAssistanceResponse)
async def assist_user(
    request: AIAssistanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get AI-driven assistance for various tasks (form filling, chat, etc.)"""
    service = AIAssistanceService(db)

    try:
        # Pass the tenant_id of the user to ensure the correct AI provider is used
        result = await service.assist(
            task_type=request.task_type,
            user_input=request.user_input,
            reference_image=request.reference_image,
            context=request.context,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            images=request.images,
        )

        return AIAssistanceResponse(
            task_type=request.task_type,
            suggested_data=result.get("suggested_data"),
            suggested_icons=result.get("suggested_icons"),
            svg_content=result.get("svg_content"),
            justification=result.get("justification"),
            message=result.get("message"),
            success=True,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("AI assistance failed")
        # Re-raise so the global handler returns a generic 500 + correlation
        # id. LLM/DB internals must not leak to the client.
        raise


@router.post("/stream")
async def assist_user_stream(
    request: AIAssistanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Stream AI-driven assistance for chat tasks"""
    if request.task_type != "chat":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is only supported for chat tasks",
        )

    service = AIAssistanceService(db)

    async def event_generator():
        try:
            async for chunk in await service.assist(
                task_type=request.task_type,
                user_input=request.user_input,
                reference_image=request.reference_image,
                context=request.context,
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                stream=True,
                images=request.images,
            ):
                payload = json.dumps({"content": chunk})
                yield f"data: {payload}\n\n"
        except ValueError as e:
            # Includes ImageValidationError (subclass of ValueError) from
            # attachment validation — surfaces as a localized, user-facing
            # guard message without leaking provider internals.
            import logging

            logging.getLogger(__name__).warning(f"Chat stream rejected: {e}")
            error_type, user_message = _classify_stream_error(e)
            error_payload = json.dumps(
                {"error": user_message, "error_type": error_type}
            )
            yield f"data: {error_payload}\n\n"
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("AI assistance streaming failed")
            error_type, user_message = _classify_stream_error(e)
            error_payload = json.dumps(
                {"error": user_message, "error_type": error_type}
            )
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions", response_model=List[ChatSessionSchema])
async def list_sessions(
    patient_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List chat sessions for the current user"""
    service = ChatSessionService(db)
    return await service.list_sessions(
        current_user.user_id, current_user.tenant_id, patient_id
    )


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageSchema])
async def get_session_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all messages for a specific chat session"""
    service = ChatSessionService(db)
    return await service.get_session_messages(
        session_id, current_user.user_id, current_user.tenant_id
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a chat session"""
    service = ChatSessionService(db)
    await service.delete_session(session_id, current_user.user_id)
    return {"success": True}


@router.get("/tools", response_model=List[AIAssistanceToolSchema])
async def list_tools(
    patient_id: UUID,
    examination_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List all available tools for the current context (including integrations)"""
    from app.ai.tools import get_tools
    from app.ai.tools.aggregator import aggregate as integration_aggregate

    built_in_tools = get_tools(
        db,
        current_user.tenant_id,
        patient_id,
        examination_id=examination_id,
        user_id=current_user.user_id,
    )

    result = []
    for t in built_in_tools:
        result.append(
            {
                "name": t.name,
                "description": t.description,
                "source": "built-in",
                "schema": t.args_schema.model_json_schema()
                if getattr(t, "args_schema", None)
                else None,
            }
        )

    try:
        integration_tools = await integration_aggregate(
            db, current_user.user_id, current_user.tenant_id, patient_id
        )
        for t in integration_tools:
            result.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "source": "integration",
                    "schema": t.args_schema.model_json_schema()
                    if getattr(t, "args_schema", None)
                    else None,
                }
            )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Failed to load integration tools for /tools: {e}"
        )

    return result


@router.post("/sessions/{session_id}/tasks/{proposal_id}/resolve")
async def resolve_hitl_task(
    session_id: UUID,
    proposal_id: str,
    resolution: HitlResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Record the human resolution of a human-in-the-loop task card.

    The frontend commits the actual write through the canonical REST endpoint
    (e.g. POST /clinical-events) BEFORE calling this. This endpoint only records
    the outcome into the task JSONB (for audit + agent awareness); it does not
    perform the write itself. Idempotent: a second resolve on the same proposal
    returns 409.
    """
    # Pydantic coerces `status` to HitlTaskStatus already; this is a defense-
    # in-depth check in case the schema is bypassed (it accepts only the two
    # resolution transitions, not PROPOSED/FAILED which are server-set).
    if resolution.status not in (HitlTaskStatus.CONFIRMED, HitlTaskStatus.DISMISSED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be 'confirmed' or 'dismissed'",
        )

    chat_service = ChatSessionService(db)
    message = await chat_service.find_message_by_proposal(
        session_id, proposal_id, current_user.user_id, current_user.tenant_id
    )
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task proposal not found or access denied.",
        )

    tasks = list(message.tasks or [])
    task_idx = next(
        (
            i
            for i, t in enumerate(tasks)
            if isinstance(t, dict) and t.get("proposal_id") == proposal_id
        ),
        None,
    )
    if task_idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task proposal not found."
        )

    task = tasks[task_idx]
    prior_status = task.get("status")
    if prior_status not in (None, HitlTaskStatus.PROPOSED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task already {prior_status}.",
        )

    now = datetime.now(timezone.utc).isoformat()
    task["status"] = resolution.status
    task["resolved"] = {
        "confirmed_by": str(current_user.user_id),
        "final_payload": resolution.final_payload,
        "result": resolution.result,
        "error": resolution.error,
        "at": now,
    }
    tasks[task_idx] = task
    await chat_service.update_message_tasks(message, tasks)

    # Agent awareness: append a concise assistant note so the next chat turn
    # knows the proposed action was carried out (or dismissed) by the user.
    #
    # ``ask_user`` tasks are read-only (no REST write happens) and the user's
    # answers are surfaced in full on the immediately-following resume turn
    # via the [HITL RESOLUTION FEEDBACK] message. Repeating them here would
    # be redundant AND would say "saved" (misleading — nothing was saved),
    # so the audit note for ask_user explicitly says "answered" instead.
    task_type = task.get("task_type")
    if resolution.status == HitlTaskStatus.CONFIRMED:
        payload = task.get("proposed_payload") or {}
        label = payload.get("title") or task.get("title") or task.get("task_type")
        if task_type == "ask_user":
            note_text = f"✓ The user answered: {label}."
        else:
            note_text = f"✓ Confirmed and saved: {label}."
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={"text": note_text},
        )
    elif resolution.status == HitlTaskStatus.DISMISSED:
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={
                "text": "The proposed draft was dismissed. Let me know how you'd like to proceed."
            },
        )

    return {"success": True, "task": task}


@router.post("/sessions/{session_id}/resume")
async def resume_hitl_session(
    session_id: UUID,
    body: HitlResumeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Trigger an agent continuation turn after the user has resolved one or
    more HITL task cards in the session.

    Returns an SSE stream (same shape as `/stream`). The agent receives a
    structured `[HITL RESOLUTION FEEDBACK]` user message built from the target
    message's resolved tasks (read from `tasks` JSONB on the server — never
    trusted from the request body).

    Guards:
      * Session ownership verified (user_id + tenant_id).
      * Target message must exist and have tasks.
      * Every task on the target message must be in a terminal state.
    """
    service = AIAssistanceService(db)

    async def event_generator():
        try:
            # `resume_after_hitl` is an async generator (it uses `yield`), so
            # calling it returns the generator directly — do NOT await it.
            # Awaiting would raise: "object async_generator can't be used in
            # 'await' expression".
            async for chunk in service.resume_after_hitl(
                session_id=session_id,
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                message_id=body.message_id,
            ):
                payload = json.dumps({"content": chunk})
                yield f"data: {payload}\n\n"
        except ValueError as e:
            # Client-facing guard violations (ownership, no tasks, pending).
            import logging

            logging.getLogger(__name__).warning(f"HITL resume rejected: {e}")
            error_type, user_message = _classify_stream_error(e)
            error_payload = json.dumps(
                {"error": user_message, "error_type": error_type}
            )
            yield f"data: {error_payload}\n\n"
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("HITL resume streaming failed")
            error_type, user_message = _classify_stream_error(e)
            error_payload = json.dumps(
                {"error": user_message, "error_type": error_type}
            )
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Transcribe a short voice recording to text (speech-to-text).

    Accepts a compressed audio upload (Opus/WebM preferred), validates size +
    MIME, resolves the ``transcription`` task assignment (a model advertising
    the ``audio_input`` capability), and returns ``{"text": "..."}``.

    The audio is **ephemeral**: it is streamed to the STT provider and never
    persisted to the DB or object storage (audio may contain PHI). The
    resulting text is what the client places in the chat input box; it then
    flows through the normal chat pipeline (prompt guard + HITL wall) on send.
    """
    # MediaRecorder emits a full content type WITH parameters, e.g.
    # ``audio/webm;codecs=opus``. Normalize to the bare subtype (everything
    # before the first ``;``) so the allowlist matches.
    raw_mime = (file.content_type or "").split(";")[0].strip().lower()
    if raw_mime and raw_mime not in _ALLOWED_AUDIO_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported audio format. Use WebM/Opus, OGG, MP4, or WAV.",
        )

    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio recording.",
        )
    if len(audio) > settings.AI_STT_MAX_AUDIO_BYTES:
        limit_mb = settings.AI_STT_MAX_AUDIO_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio exceeds the {limit_mb:.0f} MiB limit.",
        )

    filename = file.filename or "recording.webm"
    # Prefer the bare mime for the provider payload (some STT APIs reject the
    # ``;codecs=`` parameter); fall back to the original if normalization
    # yielded nothing.
    mime_for_provider = raw_mime or (file.content_type or "audio/webm")

    service = AIAssistanceService(db)
    try:
        target = await service.ai_provider_service.get_stt_target(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id
        )
    except TranscriptionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        text = await transcribe_audio(
            audio,
            filename=filename,
            mime_type=mime_for_provider,
            target=target,
        )
    except TranscriptionError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    # Run the transcribed text through the prompt-injection guard for audit
    # correlation (non-blocking) — consistent with every other user input path.
    if text:
        check_user_input_safety(text, context="transcription")

    return {"text": text, "success": bool(text)}

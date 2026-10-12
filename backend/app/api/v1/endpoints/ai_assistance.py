from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.assistance.service import AIAssistanceService
from app.ai.assistance.stt import TranscriptionError, transcribe_audio
from app.ai.assistance.turns import (
    TURN_LOCK_MESSAGE,
    TurnConflictError,
    acquire_turn,
    release_turn,
    stream_error_frame,
)
from app.ai.schemas.assistance import (
    AIAssistanceRequest,
    AIAssistanceResponse,
    AIAssistanceToolSchema,
    ChatBranchContext,
    ChatBranchNodeSchema,
    ChatMessageEditRequest,
    ChatMessageSchema,
    ChatSessionSchema,
    ChatSessionTitleUpdate,
    ChatTreeSchema,
    HitlResolutionRequest,
    HitlResumeRequest,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit_user
from app.core.security import get_current_user
from app.models.enums import HitlTaskStatus  # noqa: F401 -- re-exported for schema consumers
from app.schemas.user import TokenData
from app.services.chat_session_service import ChatSessionService, is_internal_message
from app.services.hitl_resolver import (
    HitlResolutionConflict,
    HitlResolutionError,
    resolve_chat_task,
)
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


async def _validate_owned_session(context: dict, current_user: TokenData, db: AsyncSession) -> None:
    """Audit 2026-09-11 S-1: a client-supplied session_id must belong to the
    caller before chat dispatch (defense-in-depth alongside the service-level
    check; a foreign id is a client bug — 404, not silent auto-create)."""
    sid = context.get("session_id")
    if not sid:
        return
    from app.services.chat_session_service import ChatSessionService

    session = await ChatSessionService(db).get_owned_session(
        UUID(sid), current_user.user_id, current_user.tenant_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )


async def _validate_patient_context(
    context_or_id, current_user: TokenData, db: AsyncSession
) -> None:
    """API-H3 (audit 2026-08): a client-supplied patient_id is access-checked
    before any AI tool pipeline binds it. Previously a USER could chat about
    (and export via tools) any tenant patient's PHI."""
    from app.services.access import check_patient_access

    pid = context_or_id.get("patient_id") if isinstance(context_or_id, dict) else context_or_id
    if pid:
        await check_patient_access(pid, current_user, db)


@router.post("/assist", response_model=AIAssistanceResponse)
async def assist_user(
    request: AIAssistanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_assist", max_requests=30, window=300)),
):
    """Get AI-driven assistance for various tasks (form filling, chat, etc.)"""
    await _validate_patient_context(request.context, current_user, db)
    await _validate_owned_session(request.context, current_user, db)
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
    _rl: None = Depends(rate_limit_user("ai_stream", max_requests=30, window=300)),
):
    """Stream AI-driven assistance for chat tasks.

    SSE frames carry the family chat vocabulary (ai-features §5) plus
    health's payload events (``citation`` / ``task`` / ``interrupt`` /
    ``session``) — one JSON object per frame with the name in ``event``."""
    if request.task_type != "chat":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is only supported for chat tasks",
        )

    await _validate_patient_context(request.context, current_user, db)
    await _validate_owned_session(request.context, current_user, db)
    service = AIAssistanceService(db)

    context_session = (request.context or {}).get("session_id")
    lock_session_id = UUID(context_session) if context_session else None
    lock_token = None
    if lock_session_id is not None:
        try:
            lock_token = await acquire_turn(lock_session_id)
        except TurnConflictError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=TURN_LOCK_MESSAGE,
            ) from e

    async def event_generator():
        try:
            async for frame in await service.assist(
                task_type=request.task_type,
                user_input=request.user_input,
                reference_image=request.reference_image,
                context=request.context,
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                stream=True,
                images=request.images,
            ):
                yield frame
        except ValueError as e:
            # Includes ImageValidationError (subclass of ValueError) from
            # attachment validation — surfaces as a localized, user-facing
            # guard message without leaking provider internals.
            import logging

            logging.getLogger(__name__).warning(f"Chat stream rejected: {e}")
            yield stream_error_frame(e)
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("AI assistance streaming failed")
            yield stream_error_frame(e)
        finally:
            if lock_session_id is not None:
                await release_turn(lock_session_id, lock_token)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions", response_model=list[ChatSessionSchema])
async def list_sessions(
    patient_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List chat sessions for the current user"""
    service = ChatSessionService(db)
    return await service.list_sessions(current_user.user_id, current_user.tenant_id, patient_id)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageSchema])
async def get_session_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """The visible (active-path) messages of a session — the branch-tree
    pointer walk (plan 13 H2) — decorated with variant info
    (``parent_id`` / ``variant_index`` / ``variant_count`` / ``sibling_ids``)."""
    service = ChatSessionService(db)
    messages = await service.get_active_path_decorated(
        session_id, current_user.user_id, current_user.tenant_id
    )
    # Hide LLM-internal artifacts (e.g. the [HITL RESOLUTION FEEDBACK] user
    # message persisted by the resume continuation). These still reconstruct
    # into LLM history via the shared service method — only the UI omits them.
    return [m for m in messages if not is_internal_message(m.content)]


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


@router.patch("/sessions/{session_id}", response_model=ChatSessionSchema)
async def rename_session(
    session_id: UUID,
    data: ChatSessionTitleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Rename a chat session (family B3 contract: tenant-scoped title PATCH)."""
    service = ChatSessionService(db)
    session = await service.rename_session(
        session_id, current_user.user_id, current_user.tenant_id, data.title
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return session


async def _branch_turn_stream(
    message_id: UUID,
    current_user: TokenData,
    db: AsyncSession,
    branch: dict,
    user_input: str,
    context: dict | None = None,
):
    """Shared SSE turn stream for the branching endpoints (edit /
    regenerate): read-only pre-validation (tenant-scoped ownership + role,
    404/400 before the stream starts), the per-session turn lock, then the
    same family vocabulary as ``/stream``. The branch mutation itself runs
    inside the stream (service-validated again) while the lock is held."""
    chat_service = ChatSessionService(db)
    message = await chat_service.get_message(
        message_id, current_user.user_id, current_user.tenant_id
    )
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if branch.get("kind") == "edit" and message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only user messages can be edited"
        )
    if branch.get("kind") == "regenerate" and message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Regenerate targets a user message",
        )
    session = await chat_service.get_owned_session(
        message.session_id, current_user.user_id, current_user.tenant_id
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    try:
        lock_token = await acquire_turn(session.id)
    except TurnConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=TURN_LOCK_MESSAGE,
        ) from e
    service = AIAssistanceService(db)

    async def event_generator():
        try:
            async for frame in await service.assist(
                task_type="chat",
                user_input=user_input,
                context=context or {},
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                stream=True,
                branch=branch | {"message_id": str(message_id)},
            ):
                yield frame
        except ValueError as e:
            import logging

            logging.getLogger(__name__).warning(f"Branch turn rejected: {e}")
            yield stream_error_frame(e)
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("Branch turn streaming failed")
            yield stream_error_frame(e)
        finally:
            await release_turn(session.id, lock_token)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/messages/{message_id}/edit")
async def edit_message(
    message_id: UUID,
    data: ChatMessageEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_stream", max_requests=30, window=300)),
):
    """Edit-and-resend (family branching contract): branch the user message
    as a new sibling under the same parent, flip the parent's active-child
    pointer, and stream a fresh reply for the edited prompt. The old subtree
    stays as a switchable variant."""
    return await _branch_turn_stream(
        message_id, current_user, db, {"kind": "edit"}, data.content, data.context
    )


@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: UUID,
    data: ChatBranchContext | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_stream", max_requests=30, window=300)),
):
    """Regenerate (family branching contract): stream a fresh assistant
    sibling under the target user message; earlier replies stay switchable
    variants — never overwritten."""
    return await _branch_turn_stream(
        message_id,
        current_user,
        db,
        {"kind": "regenerate"},
        "",
        data.context if data is not None else None,
    )


@router.post("/messages/{message_id}/select", response_model=list[ChatMessageSchema])
async def select_message(
    message_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> list[ChatMessageSchema]:
    """Flip exactly one branch pointer (level-flip semantics — no
    ancestor-chain activation) and return the re-walked visible path."""
    service = ChatSessionService(db)
    try:
        rows = await service.select_message(
            message_id, current_user.user_id, current_user.tenant_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [m for m in rows if not is_internal_message(m.content)]


@router.get("/sessions/{session_id}/tree", response_model=ChatTreeSchema)
async def get_session_tree(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
) -> ChatTreeSchema:
    """Read-only branch-tree projection ``{active_root_id, nodes[]}`` for
    the tree rail (family branching contract)."""
    service = ChatSessionService(db)
    projection = await service.tree(session_id, current_user.user_id, current_user.tenant_id)
    if projection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return ChatTreeSchema(
        active_root_id=projection["active_root_id"],
        nodes=[ChatBranchNodeSchema(**node) for node in projection["nodes"]],
    )


@router.get("/tools", response_model=list[AIAssistanceToolSchema])
async def list_tools(
    patient_id: UUID,
    examination_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List all available tools for the current context (including integrations)"""
    from app.ai.tools import get_tools
    from app.ai.tools.aggregator import aggregate as integration_aggregate

    await _validate_patient_context(patient_id, current_user, db)
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

        logging.getLogger(__name__).warning(f"Failed to load integration tools for /tools: {e}")

    return result


@router.post("/sessions/{session_id}/tasks/{proposal_id}/resolve")
async def resolve_hitl_task(
    session_id: UUID,
    proposal_id: str,
    resolution: HitlResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_resolve", max_requests=30, window=300)),
):
    """Record the human resolution of a human-in-the-loop task card.

    The frontend commits the actual write through the canonical REST endpoint
    (e.g. POST /clinical-events) BEFORE calling this. This endpoint only records
    the outcome into the task JSONB (for audit + agent awareness); it does not
    perform the write itself. Idempotent: a second resolve on the same proposal
    returns 409.

    The logic lives in ``app.services.hitl_resolver`` (shared with the
    health-assistant bridge, R7 Phase 1); this endpoint maps its typed
    errors to HTTP.
    """
    chat_service = ChatSessionService(db)
    try:
        task = await resolve_chat_task(
            chat_service,
            session_id=session_id,
            proposal_id=proposal_id,
            status=resolution.status,
            final_payload=resolution.final_payload,
            result=resolution.result,
            error=resolution.error,
            user_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
        )
    except HitlResolutionConflict as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except HitlResolutionError as e:
        message = str(e)
        status_code = (
            status.HTTP_400_BAD_REQUEST
            if message.startswith("status must be")
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=status_code, detail=message) from e

    return {"success": True, "task": task}


@router.post("/sessions/{session_id}/resume")
async def resume_hitl_session(
    session_id: UUID,
    body: HitlResumeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_resume", max_requests=30, window=300)),
):
    """Trigger an agent continuation turn after the user has resolved one or
    more HITL task cards in the session.

    Returns an SSE stream (same family vocabulary as `/stream`). The agent
    receives a structured `[HITL RESOLUTION FEEDBACK]` user message built
    from the target message's resolved tasks (read from `tasks` JSONB on
    the server — never trusted from the request body).

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
            async for frame in service.resume_after_hitl(
                session_id=session_id,
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                message_id=body.message_id,
            ):
                yield frame
        except ValueError as e:
            # Client-facing guard violations (ownership, no tasks, pending).
            import logging

            logging.getLogger(__name__).warning(f"HITL resume rejected: {e}")
            yield stream_error_frame(e)
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("HITL resume streaming failed")
            yield stream_error_frame(e)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
    _rl: None = Depends(rate_limit_user("ai_transcribe", max_requests=15, window=300)),
):
    """Transcribe a short voice recording to text (speech-to-text).

    Accepts a compressed audio upload (Opus/WebM preferred), validates size +
    MIME, resolves the ``transcription`` task assignment (a model advertising
    the ``stt`` capability), and returns ``{"text": "..."}``.

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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))  # noqa: B904 -- legacy raise; add explicit chaining when touched

    try:
        text = await transcribe_audio(
            audio,
            filename=filename,
            mime_type=mime_for_provider,
            target=target,
        )
    except TranscriptionError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))  # noqa: B904 -- legacy raise; add explicit chaining when touched

    # Run the transcribed text through the prompt-injection guard for audit
    # correlation (non-blocking) — consistent with every other user input path.
    if text:
        check_user_input_safety(text, context="transcription")

    return {"text": text, "success": bool(text)}

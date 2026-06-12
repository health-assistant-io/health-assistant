from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
import json
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any, List
from uuid import UUID

from app.core.database import get_db
from app.services.ai_assistance_service import AIAssistanceService
from app.services.chat_session_service import ChatSessionService
from app.schemas.ai_assistance import (
    AIAssistanceRequest,
    AIAssistanceResponse,
    ChatSessionSchema,
    ChatMessageSchema,
)
from app.core.security import get_current_user
from app.schemas.user import TokenData

router = APIRouter(prefix="/ai-assistance", tags=["AI Assistance"])


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
    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("AI assistance failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI assistance failed: {str(e)}",
        )


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
            ):
                payload = json.dumps({"content": chunk})
                yield f"data: {payload}\n\n"
        except Exception as e:
            import logging

            logging.getLogger(__name__).exception("AI assistance streaming failed")
            error_payload = json.dumps({"error": str(e)})
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

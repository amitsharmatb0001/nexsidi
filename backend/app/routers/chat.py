"""Chat API routes: sessions and messages.

All routes under /api/v1/chat/. Tenant-scoped.
Chat sessions are per-project and track the conversation with AI agents.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.dependencies import CurrentContext, TenantSession
from app.models.chat import ChatMessage, ChatSession
from app.schemas.project import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/sessions/{project_id}",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    project_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> ChatSessionResponse:
    """Create a new chat session for a project."""
    chat_session = ChatSession(
        organization_id=uuid.UUID(ctx.organization_id),
        project_id=project_id,
        user_id=uuid.UUID(ctx.user_id),
        channel="web",
        status="active",
    )
    session.add(chat_session)
    await session.flush()

    logger.info("chat_session_created", session_id=str(chat_session.id), project_id=str(project_id))
    return ChatSessionResponse.model_validate(chat_session)


@router.get("/sessions/{project_id}", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    project_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
    offset: int = 0,
    limit: int = 50,
) -> ChatSessionListResponse:
    """List chat sessions for a project."""
    total_q = await session.execute(
        select(func.count())
        .select_from(ChatSession)
        .where(ChatSession.project_id == project_id)
    )
    total = total_q.scalar_one()

    result = await session.execute(
        select(ChatSession)
        .where(ChatSession.project_id == project_id)
        .order_by(ChatSession.updated_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    sessions = [ChatSessionResponse.model_validate(s) for s in result.scalars().all()]

    return ChatSessionListResponse(sessions=sessions, total=total)


@router.get("/session/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> ChatSessionResponse:
    """Get a chat session with its messages."""
    result = await session.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return ChatSessionResponse.model_validate(chat)


@router.post(
    "/session/{session_id}/messages",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    session_id: uuid.UUID,
    body: ChatMessageCreate,
    ctx: CurrentContext,
    session: TenantSession,
) -> ChatMessageResponse:
    """Send a message in a chat session.

    The message is stored, and in production triggers the AI agent
    to generate a response asynchronously (via WebSocket or polling).
    """
    # Verify session exists
    result = await session.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    message = ChatMessage(
        organization_id=uuid.UUID(ctx.organization_id),
        session_id=session_id,
        role="user",
        content=body.content,
        attachments=body.attachments,
    )
    session.add(message)
    await session.flush()

    logger.info(
        "chat_message_sent",
        session_id=str(session_id),
        role="user",
        length=len(body.content),
    )

    return ChatMessageResponse.model_validate(message)


@router.get("/session/{session_id}/messages", response_model=list[ChatMessageResponse])
async def get_messages(
    session_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
    offset: int = 0,
    limit: int = 100,
) -> list[ChatMessageResponse]:
    """Get messages for a chat session (paginated, oldest first)."""
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(min(limit, 200))
    )
    return [ChatMessageResponse.model_validate(m) for m in result.scalars().all()]

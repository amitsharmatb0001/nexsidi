"""Chat API routes: sessions and messages.

All routes under /api/v1/chat/. Tenant-scoped.
Chat sessions are per-project and track the conversation with AI agents.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentContext, TenantSession
from app.models.chat import ChatMessage, ChatSession
from app.models.core import Project
from app.schemas.project import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionListItemResponse,
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
    # R-01-FIX: Verify project belongs to this tenant before creating session.
    # Without this, any user could create a chat session for another org's project.
    # R28-FIX-6: Exclude archived projects (consistent with R27-FIX-7).
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application
    # level. RLS alone is insufficient if the DB user bypasses policies.
    proj_check = await session.execute(
        select(Project.id).where(
            Project.id == project_id,
            Project.organization_id == uuid.UUID(ctx.organization_id),
            Project.status != "archived",
        )
    )
    if proj_check.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    chat_session = ChatSession(
        organization_id=uuid.UUID(ctx.organization_id),
        project_id=project_id,
        user_id=uuid.UUID(ctx.user_id),
        channel="web",
        status="active",
    )
    session.add(chat_session)
    await session.flush()

    # R31-FIX: Eagerly load the `messages` relationship before serializing.
    # ChatSessionResponse has a `messages: list[ChatMessageResponse]` field.
    # Without this, Pydantic's model_validate() accesses chat_session.messages
    # via SQLAlchemy lazy-loading, which crashes with MissingGreenlet because
    # we're in an async context. For a newly created session, messages is empty.
    await session.refresh(chat_session, ["messages"])

    logger.info("chat_session_created", session_id=str(chat_session.id), project_id=str(project_id))
    return ChatSessionResponse.model_validate(chat_session)


@router.get("/sessions/{project_id}", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    project_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
) -> ChatSessionListResponse:
    """List chat sessions for a project.

    R26-FIX-31: Filters by user_id. Previously returned all users' sessions
    for the project, leaking session metadata (IDOR).
    R26-FIX-4: Eagerly loads messages via selectinload to prevent
    MissingGreenlet crash from async lazy-loading.
    """
    user_id = uuid.UUID(ctx.user_id)
    org_id = uuid.UUID(ctx.organization_id)

    # R32-FIX-IDOR: Defense-in-depth — filter by organization_id at application
    # level, consistent with R31-FIX-IDOR on project endpoints.
    session_filter = (
        ChatSession.project_id == project_id,
        ChatSession.user_id == user_id,
        ChatSession.organization_id == org_id,
    )

    total_q = await session.execute(
        select(func.count())
        .select_from(ChatSession)
        .where(*session_filter)
    )
    total = total_q.scalar_one()

    # R28-FIX-7: Remove selectinload for list endpoint — loading ALL messages
    # for every session in a paginated list is a memory bomb (50 sessions ×
    # 200 messages = 10,000 ChatMessage objects). Messages are loaded via
    # get_chat_session (single session) or get_messages (paginated).
    result = await session.execute(
        select(ChatSession)
        .where(*session_filter)
        .order_by(ChatSession.updated_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    # R29-FIX-2: Use ChatSessionListItemResponse (no messages field) to prevent
    # MissingGreenlet from async lazy-loading of the unloaded relationship.
    sessions = [ChatSessionListItemResponse.model_validate(s) for s in result.scalars().all()]

    return ChatSessionListResponse(sessions=sessions, total=total)


@router.get("/session/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: uuid.UUID,
    ctx: CurrentContext,
    session: TenantSession,
) -> ChatSessionResponse:
    """Get a chat session with its messages.

    R26-FIX-4: Eagerly loads messages via selectinload to prevent
    MissingGreenlet crash from async lazy-loading.
    """
    # R25-FIX-9: Verify session belongs to this user.
    # R32-FIX-IDOR: Defense-in-depth — filter by organization_id at application level.
    result = await session.execute(
        select(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == uuid.UUID(ctx.user_id),
            ChatSession.organization_id == uuid.UUID(ctx.organization_id),
        )
        .options(selectinload(ChatSession.messages))
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
    # R25-FIX-9: Verify session exists AND belongs to this user.
    # Previously any org user could inject messages into any other
    # user's chat session by knowing the session_id UUID.
    # R32-FIX-IDOR: Defense-in-depth — filter by organization_id at application level.
    # R34-FIX: Also require session status == "active". Previously, a closed
    # or archived session still accepted new messages, polluting the history
    # and potentially triggering AI responses on a "closed" conversation.
    result = await session.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == uuid.UUID(ctx.user_id),
            ChatSession.organization_id == uuid.UUID(ctx.organization_id),
            ChatSession.status == "active",
        )
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

    # R30-FIX-10: Update ChatSession.updated_at on new message. Without this,
    # the session list (ordered by updated_at DESC) doesn't reflect recent
    # activity — a session with 100 new messages sorts the same as one that's
    # been idle for months.
    from datetime import datetime, timezone
    chat.updated_at = datetime.now(timezone.utc)

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
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=100, ge=1, le=200, description="Items per page"),
) -> list[ChatMessageResponse]:
    """Get messages for a chat session (paginated, oldest first)."""
    # R25-FIX-9: Verify the session belongs to this user before returning messages.
    # Previously any org user could read any other user's chat messages.
    # R32-FIX-IDOR: Defense-in-depth — filter by organization_id at application level.
    session_check = await session.execute(
        select(ChatSession.id).where(
            ChatSession.id == session_id,
            ChatSession.user_id == uuid.UUID(ctx.user_id),
            ChatSession.organization_id == uuid.UUID(ctx.organization_id),
        )
    )
    if session_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    # R34-FIX: Defense-in-depth — filter ChatMessage by organization_id too.
    # Previously only filtered by session_id. If RLS is bypassed (superuser
    # connection, misconfigured policy), messages from any org could leak.
    # This matches the R31/R32 IDOR pattern applied to every other entity query.
    result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.organization_id == uuid.UUID(ctx.organization_id),
        )
        .order_by(ChatMessage.created_at.asc())
        .offset(offset)
        .limit(min(limit, 200))
    )
    return [ChatMessageResponse.model_validate(m) for m in result.scalars().all()]

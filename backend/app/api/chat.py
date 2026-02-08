"""
CHAT API ENDPOINT
=================
This file handles all chat-related operations between users and Tilotma.

Think of this as a message relay system:
- User sends message → This file catches it
- This file gives message to Tilotma agent
- Tilotma thinks and responds
- This file sends response back to user

Why we need this:
- Users can't directly talk to Tilotma (she's just Python code)
- We need an HTTP endpoint (web address) they can send messages to
- This converts web requests into agent function calls
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID, uuid4
from datetime import datetime
from app.database import get_db
from app.models import User, Conversation
from app.schemas import MessageCreate, MessageResponse, ChatContextResponse
from app.dependencies import get_current_user
from app.agents.tilotma import Tilotma, Message, ConversationPhase
from app.agents.arjun import Arjun
from app.models import Project
from app.services.queue_manager import QueueManager
from app.core.rate_limit import rate_limiter
from app.services.context_engine import context_engine
from fastapi import Request

# Create router
# A "router" is like a section of your restaurant's menu
# This router handles all /api/chat/* URLs
router = APIRouter()


@router.post("/send", response_model=dict)
async def send_message(
    request: Request,
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    MAIN CHAT ENDPOINT
    ==================
    
    What happens when user sends a message:
    
    Step 1: User sends message (from React frontend)
    Step 2: This function receives it
    Step 3: We verify user is logged in (get_current_user does this)
    Step 4: Create Tilotma agent instance
    Step 5: Give message to Tilotma
    Step 6: Tilotma processes and responds
    Step 7: Send response back to user
    
    Security:
    - Only logged-in users can chat (current_user: User = Depends(get_current_user))
    - Each user only sees their own conversations
    
    Parameters:
    - message_data: Contains the message text and optional project_id
    - current_user: Automatically injected by FastAPI (from JWT token)
    - db: Database connection (automatically provided)
    
    Returns:
    - response: Tilotma's reply text
    - should_create_project: True if user is ready to proceed with a project
    - cost: How much this API call cost (in rupees)
    
    Example request:
    POST /api/chat/send
    Headers: Authorization: Bearer <token>
    Body: {
        "content": "I want to build a restaurant website",
        "project_id": null
    }
    
    Example response:
    {
        "response": "That sounds great! What features do you need?",
        "should_create_project": false,
        "cost": 0.03
    }
    """
    
    # Check rate limit
    await rate_limiter.check(request)
    
    # Create Tilotma agent (Stateless initialization)
    # We must manually restore context from DB because agent is ephemeral
    tilotma = Tilotma(
        project_id=str(message_data.project_id) if message_data.project_id else str(uuid4()),
        user_id=str(current_user.id)
    )
    
    # RESTORE CONTEXT FROM DB
    # -----------------------
    # Fetch previous messages for this project/user
    if message_data.project_id:
        history = db.query(Conversation).filter(
            Conversation.project_id == message_data.project_id,
            Conversation.user_id == current_user.id
        ).order_by(Conversation.created_at.asc()).all()
    else:
        # Pre-project chat
        history = db.query(Conversation).filter(
            Conversation.user_id == current_user.id,
            Conversation.project_id.is_(None)
        ).order_by(Conversation.created_at.asc()).all()
        
    # Replay history into agent context
    for msg in history:
        tilotma.context.add_message(Message(
            role=msg.role,
            content=msg.content,
            timestamp=msg.created_at
        ))
    
    # RESTORE STATE FROM REDIS
    saved_state = context_engine.get_context(
        str(current_user.id),
        "tilotma_chat_state"
    )
    if saved_state:
        try:
            tilotma.phase = ConversationPhase(saved_state.get("phase", "greeting"))
            tilotma.context.requirements_detected = saved_state.get("requirements_detected", False)
        except (ValueError, AttributeError):
            pass
    
    # EXECUTE CHAT
    # ------------
    response_text = await tilotma.chat(message_data.content)
    
    # SAVE MESSAGES TO DB (Wrapped in transaction for safety)
    # -------------------
    try:
        # 1. Save User Message
        user_msg = Conversation(
            id=uuid4(),
            user_id=current_user.id,
            project_id=message_data.project_id,
            role='user',
            content=message_data.content,
            agent_name='tilotma',
            created_at=datetime.utcnow()
        )
        db.add(user_msg)
        
        # 2. Save Assistant Response
        # Get the last message from context (which is the new response)
        last_msg = tilotma.context.messages[-1]
        
        assistant_msg = Conversation(
            id=uuid4(),
            user_id=current_user.id,
            project_id=message_data.project_id,
            role='assistant',
            content=response_text,
            agent_name='tilotma',
            created_at=datetime.utcnow(),
            tokens_used=last_msg.tokens_used,
            cost=last_msg.cost
        )
        db.add(assistant_msg)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save messages to database"
        )

    # PERSIST STATE TO REDIS
    tilotma_state = {
        "phase": tilotma.phase.value,
        "requirements_detected": tilotma.context.requirements_detected,
        "confidence": getattr(tilotma, 'confidence', 0.0)
    }
    context_engine.store_context(
        str(current_user.id),
        "tilotma_chat_state",
        tilotma_state
    )
    
    # Check if user is ready to build project
    # Using Tilotma's internal state detection
    should_create = (
        hasattr(tilotma, 'phase') and 
        tilotma.phase == ConversationPhase.READY_FOR_EXECUTION
    )
    
    if should_create:
        # Create project in database directly
        new_project = Project(
            user_id=current_user.id,
            title=f"Project {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            description=message_data.content,
            status="queued",
            current_agent="saanvi"
        )
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        # Queue for processing
        queue = QueueManager()
        queue_info = await queue.enqueue_project(
            project_id=str(new_project.id),
            user_id=str(current_user.id)
        )
        
        return {
            'response': response_text,
            'should_create_project': True,
            'project_id': str(new_project.id),
            'queued': True,
            'queue_position': queue_info.get('queue_position', 1),
            'estimated_wait': queue_info.get('estimated_wait', "Starting now"),
            'cost': last_msg.cost
        }
    
    # Return response to user
    return {
        'response': response_text,
        'should_create_project': False,
        'cost': last_msg.cost
    }


@router.get("/history", response_model=ChatContextResponse)
async def get_chat_history(
    project_id: UUID = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    GET CONVERSATION HISTORY
    ========================
    
    What this does:
    Fetches all previous messages in a conversation so user can see what they discussed.
    
    Why we need this:
    - When user refreshes page, they need to see old messages
    - When continuing a conversation, need context
    - For displaying chat history in UI
    
    How it works:
    - If project_id provided: Get messages for that specific project
    - If no project_id: Get all user's messages (pre-project chat)
    
    Parameters:
    - project_id (optional): Which project's chat to fetch
    - limit: Maximum number of messages to return (default 50)
    - current_user: Automatically injected (must be logged in)
    - db: Database connection
    
    Returns:
    - messages: List of all messages (with timestamps)
    - current_agent: Which agent user is talking to (always 'tilotma' for now)
    - project_status: If project exists, what stage it's in
    
    Example request:
    GET /api/chat/history?project_id=123e4567-e89b-12d3-a456-426614174000&limit=20
    Headers: Authorization: Bearer <token>
    
    Example response:
    {
        "messages": [
            {
                "id": "...",
                "role": "user",
                "content": "Hi, I need a website",
                "agent_name": "tilotma",
                "created_at": "2025-12-31T10:30:00Z"
            },
            {
                "id": "...",
                "role": "assistant",
                "content": "Hello! I'd love to help...",
                "agent_name": "tilotma",
                "created_at": "2025-12-31T10:30:05Z"
            }
        ],
        "current_agent": "tilotma",
        "project_status": null
    }
    """
    
    try:
        # Build query to fetch messages
        query = db.query(Conversation).filter(
            Conversation.user_id == current_user.id,
            Conversation.agent_name == 'tilotma'  # Only Tilotma messages for now
        )
        
        # If specific project requested, filter by that
        if project_id:
            query = query.filter(Conversation.project_id == project_id)
        else:
            # Get pre-project chat (where project_id is null)
            query = query.filter(Conversation.project_id.is_(None))
        
        # Sort by time (oldest first) and limit
        messages = query.order_by(Conversation.created_at.asc()).limit(limit).all()
        
        # Determine current status
        # (In v1, always 'tilotma' - later versions will route to different agents)
        current_agent = 'tilotma'
        project_status = None
        
        if project_id:
            # Fetch project status if project exists
            from app.models import Project
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.user_id == current_user.id
            ).first()
            
            if project:
                project_status = project.status
        
        return {
            'messages': messages,
            'current_agent': current_agent,
            'project_status': project_status
        }
    except Exception as e:
        import logging
        logging.getLogger("chat").error(f"Error fetching chat history: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve chat history"
        )


@router.delete("/history/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    DELETE A MESSAGE
    ================
    
    What this does:
    Allows user to delete a specific message from their chat history.
    
    Why we need this:
    - User made a typo
    - User wants to remove sensitive information
    - User wants to restart conversation
    
    Security:
    - Can only delete your own messages
    - Cannot delete messages from other users' conversations
    
    Parameters:
    - message_id: Which message to delete
    - current_user: Must be logged in
    - db: Database connection
    
    Returns:
    - Nothing (204 No Content status)
    
    Example request:
    DELETE /api/chat/history/123e4567-e89b-12d3-a456-426614174000
    Headers: Authorization: Bearer <token>
    """
    
    try:
        # Find message
        message = db.query(Conversation).filter(
            Conversation.id == message_id,
            Conversation.user_id == current_user.id  # Security: only own messages
        ).first()
        
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        
        # Delete it
        db.delete(message)
        db.commit()
        
        return None  # 204 response has no body
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("chat").error(f"Error deleting message {message_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete message"
        )


@router.post("/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    project_id: UUID = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    CLEAR ALL CHAT HISTORY
    =======================
    
    What this does:
    Deletes all messages in a conversation (fresh start).
    
    Why we need this:
    - User wants to start over completely
    - Testing/debugging (clear test data)
    - User confused and wants to restart conversation flow
    
    How it works:
    - If project_id provided: Clear only that project's chat
    - If no project_id: Clear all pre-project chat for this user
    
    Security:
    - Can only clear your own conversations
    - Cannot affect other users
    
    Parameters:
    - project_id (optional): Which project's chat to clear
    - current_user: Must be logged in
    - db: Database connection
    
    Returns:
    - Nothing (204 No Content)
    
    Example request:
    POST /api/chat/clear
    Headers: Authorization: Bearer <token>
    Body: {"project_id": null}
    """
    
    try:
        # Build delete query
        query = db.query(Conversation).filter(
            Conversation.user_id == current_user.id,
            Conversation.agent_name == 'tilotma'
        )
        
        if project_id:
            query = query.filter(Conversation.project_id == project_id)
        else:
            query = query.filter(Conversation.project_id.is_(None))
        
        # Delete all matching messages
        query.delete(synchronize_session=False)
        db.commit()
        
        return None
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("chat").error(f"Error clearing chat history: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear chat history"
        )

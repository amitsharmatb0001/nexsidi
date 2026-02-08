"""
WebSocket API for Real-Time Project Monitoring
===============================================
Location: app/api/websocket.py

Purpose: Stream real-time updates to frontend:
- Agent progress
- Build status
- Error notifications
- Cost tracking
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from typing import Dict, Set
import json
import asyncio
import logging
from uuid import UUID
from app.dependencies import get_current_user_ws
from app.models import User

router = APIRouter()
logger = logging.getLogger("websocket")

# Active WebSocket connections per project
# {project_id: {websocket1, websocket2, ...}}
active_connections: Dict[str, Set[WebSocket]] = {}


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.
    
    Features:
    - Multiple clients can watch same project
    - Automatic cleanup on disconnect
    - Broadcast to all watchers of a project
    """
    
    def __init__(self):
        # {project_id: {websocket_connection1, websocket_connection2, ...}}
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.logger = logging.getLogger("connection_manager")
    
    async def connect(self, websocket: WebSocket, project_id: str):
        """
        Add new WebSocket connection for a project.
        
        Args:
            websocket: FastAPI WebSocket instance
            project_id: Project UUID to monitor
        """
        await websocket.accept()
        
        if project_id not in self.active_connections:
            self.active_connections[project_id] = set()
        
        self.active_connections[project_id].add(websocket)
        
        self.logger.info(
            f"📡 New connection for project {project_id} "
            f"(total: {len(self.active_connections[project_id])})"
        )
        
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "project_id": project_id,
            "message": "Successfully connected to project updates"
        })
    
    def disconnect(self, websocket: WebSocket, project_id: str):
        """
        Remove WebSocket connection.
        
        Args:
            websocket: FastAPI WebSocket instance
            project_id: Project UUID
        """
        if project_id in self.active_connections:
            self.active_connections[project_id].discard(websocket)
            
            # Clean up empty project entries
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]
            
            self.logger.info(
                f"📡 Disconnected from project {project_id} "
                f"(remaining: {len(self.active_connections.get(project_id, []))})"
            )
    
    async def broadcast_to_project(self, project_id: str, message: Dict):
        """
        Send update to all clients watching a project.
        
        Args:
            project_id: Project UUID
            message: Dict to send as JSON
        """
        if project_id not in self.active_connections:
            return
        
        # Get all connections for this project
        connections = list(self.active_connections[project_id])
        
        # Send to all connections
        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                self.logger.error(f"Error sending to websocket: {e}")
                disconnected.append(websocket)
        
        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect(ws, project_id)
    
    async def send_agent_progress(
        self,
        project_id: str,
        agent_name: str,
        phase: str,
        percentage: int,
        message: str = None
    ):
        """
        Send agent progress update.
        
        Example message:
        {
            "type": "agent_progress",
            "agent": "shubham",
            "phase": "generating_backend",
            "percentage": 45,
            "message": "Creating API routes..."
        }
        """
        await self.broadcast_to_project(project_id, {
            "type": "agent_progress",
            "agent": agent_name,
            "phase": phase,
            "percentage": percentage,
            "message": message or f"{agent_name} is {percentage}% complete"
        })
    
    async def send_error(
        self,
        project_id: str,
        error_type: str,
        error_message: str,
        agent_name: str = None
    ):
        """
        Send error notification.
        
        Example:
        {
            "type": "error",
            "error_type": "generation_failed",
            "message": "Backend generation failed: Missing dependencies",
            "agent": "shubham"
        }
        """
        await self.broadcast_to_project(project_id, {
            "type": "error",
            "error_type": error_type,
            "message": error_message,
            "agent": agent_name
        })
    
    async def send_cost_update(
        self,
        project_id: str,
        agent_name: str,
        cost: float,
        total_cost: float
    ):
        """
        Send cost tracking update.
        
        Example:
        {
            "type": "cost_update",
            "agent": "saanvi",
            "cost": 0.15,
            "total_cost": 2.45
        }
        """
        await self.broadcast_to_project(project_id, {
            "type": "cost_update",
            "agent": agent_name,
            "cost": cost,
            "total_cost": total_cost
        })
    
    async def send_status_change(
        self,
        project_id: str,
        old_status: str,
        new_status: str,
        current_agent: str = None
    ):
        """
        Send project status change.
        
        Example:
        {
            "type": "status_change",
            "old_status": "requirements_gathering",
            "new_status": "code_generation",
            "current_agent": "shubham"
        }
        """
        await self.broadcast_to_project(project_id, {
            "type": "status_change",
            "old_status": old_status,
            "new_status": new_status,
            "current_agent": current_agent
        })


# Global connection manager
manager = ConnectionManager()


@router.websocket("/ws/projects/{project_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    project_id: str
):
    """
    WebSocket endpoint for real-time project updates.
    
    Usage from frontend:
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/api/ws/projects/123');
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.type === 'agent_progress') {
            updateProgressBar(data.agent, data.percentage);
        } else if (data.type === 'error') {
            showError(data.message);
        }
    };
    ```
    
    Message types received:
    - connected: Initial connection confirmation
    - agent_progress: Agent working (percentage, phase)
    - error: Something went wrong
    - cost_update: Cost tracking
    - status_change: Project moved to new phase
    """
    # TODO: Add authentication
    # For now, allow any connection
    # In production: Verify user owns this project
    
    await manager.connect(websocket, project_id)
    
    try:
        while True:
            # Keep connection alive
            # Clients can send ping messages to check connection
            data = await websocket.receive_text()
            
            # Handle client messages
            try:
                message = json.loads(data)
                
                if message.get("type") == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": str(asyncio.get_event_loop().time())
                    })
                
            except json.JSONDecodeError:
                pass  # Ignore malformed messages
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id)
        logger.info(f"Client disconnected from project {project_id}")


# Helper functions for agents to send updates

async def notify_agent_progress(
    project_id: str,
    agent_name: str,
    phase: str,
    percentage: int,
    message: str = None
):
    """
    Convenience function for agents to send progress updates.
    
    Usage in agent:
        from app.api.websocket import notify_agent_progress
        
        await notify_agent_progress(
            self.project_id,
            "shubham",
            "generating_api_routes",
            45,
            "Creating user authentication routes..."
        )
    """
    await manager.send_agent_progress(
        project_id,
        agent_name,
        phase,
        percentage,
        message
    )


async def notify_error(
    project_id: str,
    error_type: str,
    error_message: str,
    agent_name: str = None
):
    """
    Convenience function for agents to send errors.
    
    Usage:
        from app.api.websocket import notify_error
        
        await notify_error(
            self.project_id,
            "generation_failed",
            "Failed to generate backend: Missing database config",
            "shubham"
        )
    """
    await manager.send_error(
        project_id,
        error_type,
        error_message,
        agent_name
    )


async def notify_cost(
    project_id: str,
    agent_name: str,
    cost: float,
    total_cost: float
):
    """
    Convenience function for cost tracking updates.
    """
    await manager.send_cost_update(
        project_id,
        agent_name,
        cost,
        total_cost
    )


async def notify_status_change(
    project_id: str,
    old_status: str,
    new_status: str,
    current_agent: str = None
):
    """
    Convenience function for status changes.
    """
    await manager.send_status_change(
        project_id,
        old_status,
        new_status,
        current_agent
    )


# Export manager for use in other modules
__all__ = [
    'router',
    'manager',
    'notify_agent_progress',
    'notify_error',
    'notify_cost',
    'notify_status_change'
]
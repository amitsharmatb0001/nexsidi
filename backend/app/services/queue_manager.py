"""
Production Queue System for NexSidi
===================================

Handles multiple projects simultaneously without resource exhaustion.

Installation:
pip install redis celery --break-system-packages

Setup:
1. Install Redis: (Windows: download from GitHub, Linux: apt install redis)
2. Start Redis: redis-server
3. Start Celery worker (Windows): celery -A app.services.queue_manager worker --loglevel=info --pool=solo
4. Start Celery worker (Linux/Mac): celery -A app.services.queue_manager worker --loglevel=info

Location: backend/app/services/queue_manager.py
"""

from app.core.celery_app import celery_app as app
from app.core.redis import redis_client

import asyncio
import time
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger("queue_manager")


class ProjectStatus(Enum):
    """Project processing status"""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

# Singleton instance
_queue_manager_instance = None


class QueueManager:
    """
    Manages project queue and concurrent execution.
    
    Features:
    - FIFO queue with position tracking
    - Maximum 3 concurrent projects
    - Maximum 10 projects in queue
    - Automatic cleanup of completed projects
    - Real-time status updates
    
    Usage:
        queue = QueueManager()
        
        # Add project to queue
        position = await queue.enqueue_project("proj-123", "user-456")
        
        # Check status
        status = await queue.get_project_status("proj-123")
        
        # Get queue info
        info = await queue.get_queue_info()
    """
    
    def __init__(self):
        self.redis = redis_client
        self.logger = logging.getLogger("queue_manager")
        
        # Configuration
        self.max_concurrent = 3  # Maximum 3 projects processing at once
        self.max_queue_size = 10  # Maximum 10 projects waiting
        
        # Redis keys
        self.queue_key = "project_queue"
        self.processing_key = "projects_processing"
        self.project_prefix = "project:"

    @classmethod
    def get_instance(cls):
        """Get or create singleton instance"""
        global _queue_manager_instance
        if _queue_manager_instance is None:
            _queue_manager_instance = cls()
        return _queue_manager_instance
    
    async def enqueue_project(
        self,
        project_id: str,
        user_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add project to queue.
        
        Args:
            project_id: UUID of the project
            user_id: UUID of the user
            metadata: Optional project metadata
        
        Returns:
            Dict with queue position and estimated wait time
        
        Raises:
            Exception if queue is full
        """
        # Check if project already queued
        if await self._is_project_queued(project_id):
            return await self.get_project_status(project_id)
        
        # Check queue size
        queue_size = await self.get_queue_size()
        
        if queue_size >= self.max_queue_size:
            raise Exception(
                f"Queue is full ({self.max_queue_size} projects). "
                "Please try again in 5-10 minutes."
            )
        
        # Add to queue (FIFO)
        position = self.redis.rpush(self.queue_key, project_id)
        
        # Store project metadata
        project_data = {
            'project_id': project_id,
            'user_id': user_id,
            'status': ProjectStatus.QUEUED.value,
            'queue_position': position,
            'queued_at': time.time(),
            'updated_at': time.time()
        }
        
        if metadata:
            project_data['metadata'] = json.dumps(metadata)
        
        self.redis.hset(
            f"{self.project_prefix}{project_id}",
            mapping=project_data
        )
        
        # Set expiry (auto-cleanup after 24 hours)
        self.redis.expire(f"{self.project_prefix}{project_id}", 86400)
        
        # Try to start processing immediately if slots available
        await self._process_queue()
        
        wait_time = self._estimate_wait_time(position)
        
        self.logger.info(
            f"📝 Project {project_id} queued at position {position}. "
            f"Estimated wait: {wait_time}"
        )
        
        return {
            'project_id': project_id,
            'status': ProjectStatus.QUEUED.value,
            'queue_position': position,
            'estimated_wait': wait_time,
            'queue_size': queue_size + 1
        }
    
    async def _process_queue(self):
        """
        Process queue - start projects if slots available.
        
        Called automatically when projects are queued.
        """
        # Get current processing count
        processing_count = self.redis.scard(self.processing_key)
        
        # Start projects while we have available slots
        while processing_count < self.max_concurrent:
            # Get next project from queue
            project_id = self.redis.lpop(self.queue_key)
            
            if not project_id:
                break  # Queue empty
            
            # Add to processing set
            self.redis.sadd(self.processing_key, project_id)
            
            # Update project status
            self.redis.hset(
                f"{self.project_prefix}{project_id}",
                mapping={
                    'status': ProjectStatus.PROCESSING.value,
                    'started_at': time.time(),
                    'updated_at': time.time()
                }
            )
            
            # Start processing task (Celery)
            process_project.delay(project_id)
            
            self.logger.info(f"🚀 Started processing project {project_id}")
            
            processing_count += 1
    
    async def mark_completed(
        self,
        project_id: str,
        result: Dict[str, Any]
    ):
        """Mark project as completed"""
        
        # Remove from processing set
        self.redis.srem(self.processing_key, project_id)
        
        # Update status
        self.redis.hset(
            f"{self.project_prefix}{project_id}",
            mapping={
                'status': ProjectStatus.COMPLETED.value,
                'completed_at': time.time(),
                'updated_at': time.time(),
                'result': json.dumps(result)
            }
        )
        
        self.logger.info(f"✅ Project {project_id} completed")
        
        # Process next in queue
        await self._process_queue()
    
    async def mark_failed(
        self,
        project_id: str,
        error: str
    ):
        """Mark project as failed"""
        
        # Remove from processing set
        self.redis.srem(self.processing_key, project_id)
        
        # Update status
        self.redis.hset(
            f"{self.project_prefix}{project_id}",
            mapping={
                'status': ProjectStatus.FAILED.value,
                'failed_at': time.time(),
                'updated_at': time.time(),
                'error': error
            }
        )
        
        self.logger.error(f"❌ Project {project_id} failed: {error}")
        
        # Process next in queue
        await self._process_queue()
    
    async def get_project_status(self, project_id: str) -> Dict[str, Any]:
        """Get current status of a project"""
        
        data = self.redis.hgetall(f"{self.project_prefix}{project_id}")
        
        if not data:
            return {'status': 'not_found'}
        
        # Add current queue position if queued
        if data.get('status') == ProjectStatus.QUEUED.value:
            position = self._get_queue_position(project_id)
            data['queue_position'] = position
            data['estimated_wait'] = self._estimate_wait_time(position)
        
        return data
    
    async def get_queue_size(self) -> int:
        """Get current queue size (waiting projects)"""
        return self.redis.llen(self.queue_key)
    
    async def get_processing_count(self) -> int:
        """Get count of currently processing projects"""
        return self.redis.scard(self.processing_key)
    
    async def get_queue_info(self) -> Dict[str, Any]:
        """Get complete queue information"""
        
        queue_size = await self.get_queue_size()
        processing_count = await self.get_processing_count()
        
        return {
            'queue_size': queue_size,
            'processing_count': processing_count,
            'max_concurrent': self.max_concurrent,
            'max_queue_size': self.max_queue_size,
            'available_slots': self.max_concurrent - processing_count,
            'status': self._get_system_status(queue_size, processing_count)
        }
    
    def _get_system_status(self, queue_size: int, processing_count: int) -> str:
        """Determine system status"""
        
        if queue_size == 0 and processing_count < self.max_concurrent:
            return "healthy"
        elif queue_size < 5:
            return "normal"
        elif queue_size < self.max_queue_size:
            return "busy"
        else:
            return "at_capacity"
    
    def _estimate_wait_time(self, position: int) -> str:
        """Estimate wait time based on queue position"""
        
        # Average project: 12-15 minutes
        # With 3 concurrent: ~5 minutes per batch of 3
        
        if position <= 3:
            return "Starting now"
        
        wait_minutes = ((position - 1) // 3) * 5
        
        if wait_minutes <= 5:
            return "1-5 minutes"
        elif wait_minutes <= 10:
            return "5-10 minutes"
        elif wait_minutes <= 15:
            return "10-15 minutes"
        else:
            return f"~{wait_minutes} minutes"
    
    def _get_queue_position(self, project_id: str) -> int:
        """Get position of project in queue"""
        
        # Get all queued projects
        queue = self.redis.lrange(self.queue_key, 0, -1)
        
        try:
            return queue.index(project_id) + 1
        except ValueError:
            return 0  # Not in queue
    
    async def _is_project_queued(self, project_id: str) -> bool:
        """Check if project is already queued or processing"""
        
        # Check if in queue
        queue = self.redis.lrange(self.queue_key, 0, -1)
        if project_id in queue:
            return True
        
        # Check if processing
        if self.redis.sismember(self.processing_key, project_id):
            return True
        
        return False


# ==============================================================================
# CELERY TASK
# ==============================================================================

@app.task(bind=True, max_retries=0)
def process_project(self, project_id: str):
    """
    Celery task to process a project.
    
    This runs in a separate worker process.
    """
    import asyncio
    from app.core.project_processor import ProjectProcessor
    
    logger.info(f"🔨 Worker processing project {project_id}")
    
    queue = QueueManager()
    
    try:
        # Process project (this is where agents run)
        processor = ProjectProcessor(project_id)
        result = asyncio.run(processor.process())
        
        # Mark completed
        asyncio.run(queue.mark_completed(project_id, result))
        
        return result
        
    except Exception as e:
        # Mark failed
        asyncio.run(queue.mark_failed(project_id, str(e)))
        
        logger.error(f"❌ Worker failed for project {project_id}: {e}")
        raise


# ==============================================================================
# USAGE EXAMPLE
# ==============================================================================

"""
# In your API endpoint:

from app.services.queue_manager import QueueManager

@app.post("/api/projects/create")
async def create_project(request: ProjectRequest):
    # Create project in database
    project = await create_project_record(request)
    
    # Add to queue
    queue = QueueManager()
    queue_info = await queue.enqueue_project(
        project_id=project.id,
        user_id=request.user_id,
        metadata={
            'project_type': request.project_type,
            'complexity': request.complexity
        }
    )
    
    return {
        'project_id': project.id,
        'status': 'queued',
        'queue_position': queue_info['queue_position'],
        'estimated_wait': queue_info['estimated_wait']
    }


# Check project status:

@app.get("/api/projects/{project_id}/status")
async def get_status(project_id: str):
    queue = QueueManager()
    status = await queue.get_project_status(project_id)
    return status


# Get queue info (for dashboard):

@app.get("/api/queue/info")
async def get_queue_info():
    queue = QueueManager()
    info = await queue.get_queue_info()
    return info
"""

"""
PROJECT PROCESSOR V2 - Thin Wrapper for Arjun
==============================================
Simplified orchestrator that delegates to Arjun (Project Manager).

NEW ARCHITECTURE:
- ProjectProcessor: Entry point, status updates, error handling
- Arjun: SDLC orchestration, agent coordination, quality gates
- Tilotma: User interaction, shadow monitoring, final validation

OLD (removed):
- Direct agent orchestration
- Adversarial review logic (now in Arjun)
- Iterative refinement (now in Arjun)
"""

import logging
import asyncio
from uuid import UUID
from datetime import datetime
from typing import Dict, Any, Optional

from app.database import get_db, SessionLocal
from app.models import Project, User, Conversation
from app.services.workspace_manager import workspace_manager

# NEW: Import Arjun instead of individual agents
from app.agents.arjun import Arjun


class ProjectProcessorV2:
    """
    Simplified Project Processor - delegates to Arjun.
    
    Responsibilities:
    1. Initialize Arjun with project context
    2. Start pipeline execution
    3. Monitor progress
    4. Update project status in database
    5. Handle errors
    
    Arjun handles:
    - Agent orchestration
    - Quality gates
    - Iterative refinement
    - Mistake memory
    - Progress reporting
    """
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.logger = logging.getLogger(f"processor_v2.{project_id}")
        self.db = SessionLocal()
        self.arjun: Optional[Arjun] = None
        
    async def process(self):
        """
        Main execution loop - delegates to Arjun.
        
        Workflow:
        1. Load project from database
        2. Create Arjun instance
        3. Prepare requirements from project description
        4. Execute Arjun's pipeline
        5. Handle completion or errors
        """
        try:
            self.logger.info("🚀 Starting project processing (V2 - Arjun orchestration)...")
            await self._update_status("processing")
            
            # 1. Load project
            project = self.db.query(Project).filter(
                Project.id == self.project_id
            ).first()
            
            if not project:
                raise ValueError(f"Project {self.project_id} not found")
            
            self.logger.info(f"📋 Project: {project.title}")
            self.logger.info(f"👤 User: {project.user_id}")
            
            # 2. Create Arjun (Project Manager)
            self.arjun = Arjun(
                project_id=self.project_id,
                user_id=str(project.user_id)
            )
            
            # 3. Prepare requirements from project description + conversation history
            # Fetch previous messages for this user (pre-project)
            history = self.db.query(Conversation).filter(
                Conversation.user_id == project.user_id,
                Conversation.project_id.is_(None)
            ).order_by(Conversation.created_at.asc()).all()
            
            conversation_history = [
                {"role": m.role, "content": m.content}
                for m in history
            ]
            
            requirements = {
                "description": project.description,
                "title": project.title,
                "user_id": str(project.user_id),
                "conversation": conversation_history, # Pass full history!
                "created_at": project.created_at.isoformat() if project.created_at else None
            }
            
            self.logger.info("🎯 Handing off to Arjun for SDLC execution...")
            
            # 4. Execute Arjun's pipeline
            # Arjun will:
            # - Call Saanvi for requirements
            # - Call Shubham for backend
            # - Call Aanya for frontend
            # - Run adversarial reviews
            # - Perform iterative refinement
            # - Call Aarav for tests
            # - Call Pranav for deployment
            # - Report all progress to Tilotma
            
            result = await self.arjun.execute_pipeline(requirements)
            
            # 5. Handle completion
            self.logger.info("✅ Arjun completed pipeline successfully!")
            await self._update_status("completed")
            
            # Store final result
            await self._store_final_result(result)
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Project processing failed: {e}")
            await self._update_status("failed")
            await self._store_error(str(e))
            raise
            
        finally:
            self.db.close()
    
    async def _update_status(self, status: str):
        """Update project status in database"""
        try:
            project = self.db.query(Project).filter(
                Project.id == self.project_id
            ).first()
            
            if project:
                project.status = status
                project.updated_at = datetime.utcnow()
                self.db.commit()
                self.logger.info(f"📊 Status updated: {status}")
        except Exception as e:
            self.logger.error(f"Failed to update status: {e}")
            self.db.rollback()
    
    async def _store_final_result(self, result):
        """Store final pipeline result"""
        try:
            # Result is already stored in context by Arjun
            # Just log completion
            self.logger.info(f"💾 Final result stored by Arjun")
            
            # Access correct attributes from PipelineResult (handle object or dict)
            if isinstance(result, dict):
                timeline = result.get('timeline', {})
            else:
                timeline = getattr(result, 'timeline', {})
            phases = timeline.get('phases', [])
            
            if timeline.get('start') and timeline.get('end'):
                from datetime import datetime
                start = datetime.fromisoformat(timeline['start'])
                end = datetime.fromisoformat(timeline['end'])
                duration = (end - start).total_seconds()
                self.logger.info(f"   Phases completed: {len(phases)}")
                self.logger.info(f"   Total time: {duration:.1f}s")
            
        except Exception as e:
            self.logger.error(f"Failed to store final result: {e}")
    
    async def _store_error(self, error: str):
        """Store error information"""
        try:
            from app.services.context_engine import context_engine
            
            context_engine.store_context(
                self.project_id,
                "processor_error",
                {
                    "error": error,
                    "timestamp": datetime.utcnow().isoformat(),
                    "phase": self.arjun.pipeline_state.current_phase.value if self.arjun else "unknown"
                }
            )
            
        except Exception as e:
            self.logger.error(f"Failed to store error: {e}")
    
    def get_progress(self) -> Dict[str, Any]:
        """
        Get current progress from Arjun.
        
        Returns:
            Progress information
        """
        if not self.arjun:
            return {
                "status": "not_started",
                "progress": 0
            }
        
        return {
            "status": "in_progress",
            "current_phase": self.arjun.pipeline_state.current_phase.value,
            "progress": self.arjun.pipeline_state.progress_percentage,
            "completed_phases": [
                phase for phase in self.arjun.pipeline_state.completed_phases
            ],
            "paused": self.arjun.pipeline_state.paused
        }


# Alias for backwards compatibility
ProjectProcessor = ProjectProcessorV2

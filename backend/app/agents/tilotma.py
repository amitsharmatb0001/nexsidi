"""
TILOTMA V2 - CHIEF AI OFFICER (Refactored)
===========================================

NEW ARCHITECTURE:
- Focused on user interaction & requirements gathering
- Delegates SDLC execution to Arjun (Project Manager)
- Shadow monitoring of all agent activities
- Final validation with deep analysis
- Dedicated memory system for deep understanding

REMOVED:
- Direct agent orchestration (now handled by Arjun)
- Pipeline execution logic (now in Arjun)

ADDED:
- handoff_to_arjun() - Delegate to Project Manager
- monitor_agent_report() - Shadow monitoring
- final_validation() - Deep analysis before delivery
- TilotmaMemory integration - Private understanding
"""

import os
import asyncio
import logging
import json
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

# Services
from app.services.ai_router import ai_router, TaskComplexity
from app.utils.json_utils import safe_json_parse
from app.services.context_engine import context_engine
from app.services.prompt_engine import prompt_engine
from app.services.tilotma_memory import TilotmaMemory, ProjectUnderstanding
from app.agents.mixins import (
    SearchCapableMixin, 
    MistakeMemoryMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ConversationPhase(Enum):
    """Current phase of conversation"""
    GREETING = "greeting"
    REQUIREMENTS_GATHERING = "requirements_gathering"
    CLARIFICATION = "clarification"
    READY_FOR_EXECUTION = "ready_for_execution"
    MONITORING_ARJUN = "monitoring_arjun"
    FINAL_VALIDATION = "final_validation"
    APPROVED = "approved"
    COMPLETED = "completed"


@dataclass
class Message:
    """Message object used by chat.py"""
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: int = 0
    cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tokens_used": self.tokens_used,
            "cost": self.cost
        }


class TilotmaContext:
    """Compatibility layer for chat history restoration"""
    def __init__(self):
        self.messages: List[Message] = []
        self.requirements_detected: bool = False
        self.phase: str = "greeting"

    def add_message(self, message: Message):
        self.messages.append(message)


@dataclass
class ValidationContext:
    """Context for Tilotma's final validation"""
    full_context: Dict[str, Any]
    agent_logs: List[Dict]
    quality_metrics: Dict[str, float]
    timeline: Dict[str, Any]
    costs: Dict[str, float]


# =============================================================================
# TILOTMA - CHIEF AI OFFICER
# =============================================================================

class Tilotma(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    Chief AI Officer - User Interface & Final Validator
    
    NEW Responsibilities:
    1. Chat with users & gather requirements
    2. Hand off to Arjun for execution
    3. Monitor all agent activities (shadow monitoring)
    4. Intervene if issues detected
    5. Final validation with deep analysis
    6. Approve/reject deliverables
    7. Post-justification to users
    
    SPECIAL FEATURES:
    - Dedicated memory system (TilotmaMemory)
    - Read-only access to all agent logs
    - Intervention authority
    - Deep thinking for critical decisions
    """
    
    def __init__(self, project_id: str, user_id: str):
        """
        Initialize Tilotma for a project.
        
        Args:
            project_id: UUID of the project
            user_id: UUID of the user who owns the project
        """
        super().__init__()
        self.project_id = project_id
        self.user_id = user_id
        self.logger = logging.getLogger(f"tilotma_v2.{project_id}")
        
        # Dedicated memory system
        self.memory = TilotmaMemory(project_id, user_id)
        
        # Current phase
        self.phase = ConversationPhase.GREETING
        
        # Arjun reference (created when needed)
        self.arjun = None
        
        # Monitoring state
        self.monitoring_log = []
        self.intervention_flags = []
        
        # Compatibility context for chat.py
        self.context = TilotmaContext()
        
        self.logger.info(f"👑 Tilotma initialized for project {project_id}")
    
    # =========================================================================
    # CHAT MODULE - User Interaction
    # =========================================================================
    
    async def chat(self, user_message: str) -> Dict:
        """
        Main chat interface - handles user messages.
        """
        await self._send_progress("user_interaction", 50, "Analyzing your request...")
        
        self.logger.info(f"💬 User message: {user_message[:100]}...")
        
        # Build conversation context
        context_msgs = []
        if hasattr(self, 'context') and self.context.messages:
            for msg in self.context.messages[-10:]:
                context_msgs.append(f"{msg.role.capitalize()}: {msg.content}")
        
        if not context_msgs:
            recent_conversations = self.memory.get_recent_conversations(count=10)
            for conv in recent_conversations:
                context_msgs.append(f"User: {conv.user_message}\nTilotma: {conv.tilotma_response}")
        
        conversation_context = "\n".join(context_msgs)
        
        # Generate response using AI
        prompt = f"""
        You are Tilotma, Chief AI Officer at NexSidi.
        
        CONVERSATION HISTORY:
        {conversation_context}
        
        CURRENT UNDERSTANDING:
        {self.memory.get_context_summary()}
        
        USER'S NEW MESSAGE:
        {user_message}
        
        YOUR TASK:
        1. Understand what the user wants.
        2. Determine if we have enough info to START development:
           - Project Type (e.g., Landing Page, Blog, SaaS)
           - Key Features (at least 3)
           - User has confirmed they want to start
        
        RESPOND IN JSON:
        {{
            "response": "friendly message to user",
            "understanding": "technical summary of the project",
            "project_type": "landing_page/blog/ecommerce/saas/social",
            "key_features": ["feature 1", "feature 2", "feature 3"],
            "tech_stack": "details (e.g. React + FastAPI)",
            "user_confirmed": true/false (has the user said 'yes', 'start', 'proceed', etc?),
            "confidence": 0.0-1.0,
            "missing_info": ["list of missing info"],
            "ready_to_start": true/false,
            "concerns": ["any concerns you have"]
        }}
        """
        
        ai_response = await ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="conversation",
            complexity=TaskComplexity.SIMPLE
        )
        
        result = safe_json_parse(ai_response.content)
        
        if not result:
            self.logger.error("❌ Failed to parse Tilotma response")
            result = {
                "response": "I'm sorry, I'm having trouble processing that request. Could you rephrase?",
                "understanding": "Error parsing AI response",
                "confidence": 0.0,
                "ready_to_start": False
            }
        
        # Save to memory
        self.memory.add_conversation(
            user_message=user_message,
            tilotma_response=result.get("response", ""),
            understanding=result.get("understanding", ""),
            concerns=result.get("concerns", []),
            decisions=[]
        )
        
        # Update project understanding
        if result.get("understanding"):
            self.memory.update_project_understanding(
                understanding=result["understanding"],
                confidence=result.get("confidence", 0.5),
                project_type=result.get("project_type"),
                key_features=result.get("key_features"),
                user_confirmed=result.get("user_confirmed", False),
                missing_info=result.get("missing_info", [])
            )
        
        # Phase 1.5: Proactive Handoff (Check if requirements complete)
        if await self._check_requirements_complete():
            self.logger.info("✅ Requirements complete - Starting project auto-handoff")
            await self._send_progress("handoff", 100, "Requirements satisfied. Handing off to project manager...")
            handoff_result = await self.handoff_to_arjun()
            return {
                "type": "project_started",
                "message": handoff_result,
                "understanding": result.get("understanding"),
                "status": "in_progress"
            }
        
        # Normal chat response
        return {
            "type": "chat_response",
            "message": result.get("response"),
            "understanding": result.get("understanding"),
            "ready_to_start": result.get("ready_to_start"),
            "missing_info": result.get("missing_info", [])
        }

    async def _check_requirements_complete(self) -> bool:
        """
        Check if we have enough information to start development
        """
        if not self.memory.project_understanding:
            return False
            
        understanding = self.memory.project_understanding
        
        has_project_type = understanding.project_type is not None
        has_features = len(understanding.key_features) >= 3
        user_confirmed = understanding.user_confirmed == True
        
        # We also need a minimum confidence
        high_confidence = understanding.confidence_level >= 0.7
        
        return has_project_type and has_features and user_confirmed and high_confidence
    
    # =========================================================================
    # HANDOFF TO ARJUN
    # =========================================================================
    
    async def handoff_to_arjun(self) -> str:
        """
        Hand off project execution to Arjun (Project Manager).
        Tilotma remains available for monitoring and intervention.
        """
        
        self.logger.info("🚀 Handing off to Arjun (Project Manager)")
        
        # Prepare requirements from Tilotma's understanding
        understanding = self.memory.project_understanding
        requirements = {
            "project_id": self.project_id,
            "user_id": self.user_id,
            "project_type": understanding.project_type,
            "description": understanding.current_understanding,
            "key_features": understanding.key_features,
            "tech_preferences": {"recommendation": "standard"},
            "conversation_history": [c.to_dict() for c in self.memory.conversation_history[-5:]]
        }
        
        # Persist handoff state (for crash recovery)
        context_engine.store_context(
            self.project_id,
            "handoff_state",
            {
                "status": "handoff_initiated",
                "from_agent": "tilotma",
                "to_agent": "arjun",
                "requirements": requirements,
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Create Arjun instance
        from app.agents.arjun import Arjun
        self.arjun = Arjun(self.project_id, self.user_id)
        
        # Start pipeline in background task
        self.phase = ConversationPhase.MONITORING_ARJUN
        task = asyncio.create_task(self._execute_arjun_pipeline(requirements))
        
        # Error handling for the task
        task.add_done_callback(self._handle_task_result)
        self.arjun_task = task
        
        return (
            "✅ I've handed this off to Arjun, our Project Manager. "
            "He'll coordinate the specialized agents to build your project. "
            "I'll monitor everything and keep you updated on progress!"
        )
    
    async def _execute_arjun_pipeline(self, requirements: Dict):
        """
        Execute Arjun's pipeline and handle final validation
        """
        try:
            self.logger.info("👁️ Monitoring Arjun's pipeline...")
            
            # Execute pipeline
            result = await self.arjun.execute_pipeline(requirements)
            
            # Update handoff state
            context_engine.store_context(
                self.project_id,
                "handoff_state",
                {
                    "status": "pipeline_completed",
                    "result": str(result), # Serialize as much as possible
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            # Pipeline complete - perform final validation
            self.phase = ConversationPhase.FINAL_VALIDATION
            await self._perform_final_validation(result)
            
        except Exception as e:
            self.logger.error(f"❌ Pipeline failed: {e}")
            
            # Update handoff state with error
            context_engine.store_context(
                self.project_id,
                "handoff_state",
                {
                    "status": "failed",
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            await self._notify_user_of_failure(e)

    def _handle_task_result(self, task: asyncio.Task):
        """
        Handle the result of a fire-and-forget task (H4).
        Logs errors and notifies the user if the task failed.
        """
        try:
            task.result()
        except asyncio.CancelledError:
            pass  # Task cancellation should not be logged as an error
        except Exception as e:
            self.logger.error(f"❌ Background task failed: {e}", exc_info=True)
            # Since this is a callback, we can't await. 
            # If we need to notify the user, we should schedule a new task.
            asyncio.create_task(self._notify_user_of_failure(e))
    
    # =========================================================================
    # SHADOW MONITORING
    # =========================================================================
    
    async def monitor_agent_report(self, agent_name: str, report: Dict):
        """
        Tilotma receives ALL agent reports in real-time.
        Can analyze and flag issues for intervention.
        
        This is called by Arjun's TilotmaReporter.
        
        Args:
            agent_name: Name of the agent reporting
            report: Report data
        """
        
        self.logger.info(f"👁️ Monitoring: {agent_name} report")
        
        # Add to monitoring log
        self.monitoring_log.append({
            "agent": agent_name,
            "report": report,
            "timestamp": time.time()
        })
        
        # Add to Tilotma's memory
        self.memory.add_monitoring_entry(agent_name, report)
        
        # AUTOMATIC VERIFICATION
        issue_detected = await self._verify_report_quality(agent_name, report)
        
        if issue_detected:
            # FLAG FOR INTERVENTION
            self.intervention_flags.append({
                "agent": agent_name,
                "issue": issue_detected,
                "report": report
            })
            
            # Add to memory
            self.memory.add_quality_concern(issue_detected["issue"])
            
            # OPTIONAL: Auto-intervene for critical issues
            if issue_detected.get("severity") == "critical":
                await self._intervene_immediately(agent_name, issue_detected)
    
    async def _verify_report_quality(
        self,
        agent_name: str,
        report: Dict
    ) -> Optional[Dict]:
        """
        Tilotma's quick verification of agent reports.
        Uses AI to detect potential issues.
        
        Returns:
            Issue dict if problem detected, None otherwise
        """
        
        # Quick AI check
        verification_prompt = f"""
        You are Tilotma, Chief AI Officer.
        
        Agent {agent_name} just submitted this report:
        {json.dumps(report, indent=2)}
        
        Quick verification:
        1. Does this look correct?
        2. Any red flags?
        3. Should I intervene?
        
        Respond JSON:
        {{
            "looks_good": true/false,
            "severity": "none"/"minor"/"critical",
            "issue": "description if any",
            "should_intervene": true/false
        }}
        """
        
        response = await ai_router.generate(
            messages=[{"role": "user", "content": verification_prompt}],
            task_type="verification",
            complexity=TaskComplexity.SIMPLE,
            max_tokens=200
        )
        
        result = safe_json_parse(response.content)
        
        if not result["looks_good"]:
            return result
        
        return None
    
    async def _intervene_immediately(self, agent_name: str, issue: Dict):
        """
        Tilotma intervenes when critical issue detected.
        
        Args:
            agent_name: Agent that needs correction
            issue: Issue details
        """
        
        self.logger.warning(f"🚨 TILOTMA INTERVENTION: {agent_name} - {issue['issue']}")
        
        # Record intervention
        self.memory.record_intervention(
            agent_name=agent_name,
            issue=issue["issue"],
            action_taken="Paused pipeline and requested correction"
        )
        
        # Stop Arjun's current operation
        if self.arjun:
            await self.arjun.pause_pipeline()
        
        # Send correction to agent
        await self._send_correction_to_agent(agent_name, issue)
        
        # Resume Arjun after correction
        if self.arjun:
            await self.arjun.resume_pipeline()

    async def _send_correction_to_agent(self, agent_name: str, issue: dict):
        """Send correction instruction to specific agent via Arjun."""
        
        correction = {
            "from": "tilotma",
            "to": agent_name,
            "issue": issue["issue"],
            "instruction": issue.get("correction", "Review and fix the identified issue"),
            "priority": "high",
            "timestamp": datetime.now().isoformat()
        }
        
        # Store correction in context for the agent to pick up
        context_engine.store_context(
            self.project_id,
            f"tilotma_correction_{agent_name}",
            correction
        )
        
        # Tell Arjun to re-run the specific agent
        if self.arjun:
            await self.arjun.rerun_agent(agent_name, correction)
    
    # =========================================================================
    # FINAL VALIDATION (Deep Analysis)
    # =========================================================================
    
    async def _perform_final_validation(self, pipeline_result) -> bool:
        """
        Tilotma's final validation with DEEP ANALYSIS.
        """
        await self._send_progress("final_validation", 20, "Initiating deep quality analysis of all deliverables...")
        self.logger.info("👑 Tilotma performing final validation with deep analysis...")
        
        # Get FULL context (all agent logs, outputs, decisions)
        full_context = self._get_validation_context()
        
        # Build comprehensive validation prompt
        validation_prompt = f"""
        You are Tilotma, Chief AI Officer, performing FINAL VALIDATION.
        
        CONTEXT ACCESS:
        - All agent logs and outputs
        - Quality review results
        - Test results
        - Deployment configuration
        
        FULL PROJECT CONTEXT:
        {json.dumps(full_context, indent=2)}
        
        PIPELINE RESULT:
        {json.dumps(pipeline_result.to_dict(), indent=2)}
        
        TILOTMA'S MEMORY:
        {self.memory.get_context_summary()}
        
        VALIDATION CHECKLIST:
        1. Requirements fully met?
        2. Code quality acceptable?
        3. Tests comprehensive and passing?
        4. Deployment ready?
        5. Any red flags or concerns?
        
        CRITICAL ANALYSIS:
        - Use your deepest analytical capabilities
        - Challenge assumptions
        - Look for edge cases
        - Verify security and performance
        
        RESPOND IN JSON:
        {{
            "approved": true/false,
            "confidence": 0.0-1.0,
            "concerns": ["list any issues"],
            "recommendations": ["suggestions"],
            "justification": "detailed explanation"
        }}
        """
        
        # Use DEEP thinking for critical validation
        await self._send_progress("final_validation", 60, "Verifying requirement traceability and cross-service logic...")
        response = await ai_router.generate(
            messages=[{"role": "user", "content": validation_prompt}],
            task_type="critical_validation",
            complexity=TaskComplexity.COMPLEX
        )
        
        # Parse validation result (H5)
        validation = safe_json_parse(response.content)
        
        if not validation or "approved" not in validation:
            self.logger.error("❌ Failed to parse validation result")
            return False
        
        if validation["approved"]:
            await self._send_progress("final_validation", 100, "Project approved. Prepare for delivery.")
            await self._approve_and_deliver(pipeline_result, validation)
        else:
            await self._send_progress("final_validation", 0, "Validation failed. Requesting corrections.")
            await self._reject_and_request_fixes(pipeline_result, validation)
        
        return validation["approved"]
    
    def _get_validation_context(self) -> Dict:
        """
        Get comprehensive context for validation.
        Tilotma has READ-ONLY access to everything.
        """
        
        return {
            "full_context": context_engine.get_full_context(self.project_id),
            "tilotma_memory": {
                "conversation_history": [
                    conv.to_dict() if hasattr(conv, "to_dict") else str(conv)
                    for conv in self.memory.conversation_history
                ],
                "project_understanding": (
                    self.memory.project_understanding.__dict__
                    if self.memory.project_understanding else None
                ),
                "interventions": self.memory.intervention_history
            },
            "monitoring_log": self.monitoring_log,
            "intervention_flags": self.intervention_flags
        }
    
    async def _approve_and_deliver(self, result, validation: Dict):
        """Approve project and deliver to user with justification"""
        
        self.logger.info("✅ Tilotma APPROVED project for delivery")
        
        self.phase = ConversationPhase.APPROVED
        
        # Store approval
        context_engine.store_context(
            self.project_id,
            "tilotma_approval",
            {
                "approved": True,
                "timestamp": time.time(),
                "validation": validation,
                "justification": validation["justification"]
            }
        )
        
        # Add to memory
        self.memory.add_conversation(
            user_message="[SYSTEM] Final validation complete",
            tilotma_response=f"Project approved: {validation['justification']}",
            understanding="Project meets all quality standards",
            decisions=["Approved for delivery"]
        )
        
        # TODO: Notify user
        message = f"""
        ✅ **Project Approved for Delivery**
        
        I've completed my final validation and I'm confident this project meets all requirements.
        
        **My Analysis:**
        {validation['justification']}
        
        **Confidence Level:** {validation['confidence'] * 100:.0f}%
        
        Your project is ready! You can download it now.
        """
        
        self.logger.info(message)
    
    async def _reject_and_request_fixes(self, pipeline_result, validation: Dict):
        """Reject output and send back to Arjun for fixes."""
        
        issues = validation.get("concerns", [])
        self.logger.warning(f"❌ Tilotma REJECTED output: {len(issues)} issues found")
        
        self.memory.record_intervention(
            agent_name="pipeline",
            issue=f"Final validation failed: {len(issues)} issues",
            action_taken="Sent back to Arjun for fixes"
        )
        
        if self.arjun:
            # Create fix request with specific issues
            fix_request = {
                "rejected_by": "tilotma",
                "issues": issues,
                "instruction": "Fix all listed issues and re-run quality checks",
                "max_retries": 2
            }
            
            # Re-run only the affected phases, not entire pipeline
            affected_agents = set()
            for issue in issues:
                issue_text = str(issue).lower()
                if "backend" in issue_text or "api" in issue_text:
                    affected_agents.add("shubham")
                elif "frontend" in issue_text or "ui" in issue_text:
                    affected_agents.add("aanya")
                elif "security" in issue_text:
                    affected_agents.add("shubham")  # Security issues are code issues
                elif "deployment" in issue_text or "docker" in issue_text:
                    affected_agents.add("pranav")
            
            # Default to backend if nothing specific detected
            if not affected_agents:
                affected_agents.add("shubham")
                
            await self.arjun.rerun_phases(
                agents=list(affected_agents),
                fix_request=fix_request
            )
    
    async def _notify_user_of_failure(self, error: Exception):
        """Notify user of pipeline failure"""
        self.logger.error(f"Notifying user of failure: {error}")
        # TODO: Implement user notification
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status for API"""
        return {
            "phase": self.phase.value,
            "project_understanding": (
                self.memory.project_understanding.__dict__
                if self.memory.project_understanding else None
            ),
            "interventions_made": len(self.memory.intervention_history),
            "monitoring_entries": len(self.monitoring_log)
        }

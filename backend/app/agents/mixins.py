"""
NexSidi Agent Mixins
====================
Core capabilities shared across all agents:
1. Progress Reporting (WebSocket)
2. Mistake Memory (Learning from failures)
3. Permanent Memory (General knowledge)
4. Context Management (Token limits)
5. Decision Ledger (Reasoning tracking)
"""

import logging
from typing import Dict, List, Optional, Callable, Any
import json

# =============================================================================
# 1. PROGRESS REPORTING (Highest Priority for initialization)
# =============================================================================

class ProgressMixin:
    """Mixin for real-time WebSocket progress updates."""
    async def _send_progress(self, phase: str, percentage: int, message: str = None):
        # Lazy import to avoid circular dependencies
        try:
            from app.api.websocket import notify_agent_progress
        except ImportError:
            # Fallback if websocket is not yet available or in a cycle
            if hasattr(self, "logger"):
                self.logger.warning("WebSocket notify_agent_progress not available")
            return
            
        project_id = getattr(self, "project_id", None)
        agent_name = getattr(self, "agent_name", self.__class__.__name__.lower())
        
        if project_id:
            try:
                await notify_agent_progress(
                    project_id=project_id,
                    agent_name=agent_name,
                    phase=phase,
                    percentage=percentage,
                    message=message
                )
                if hasattr(self, "logger"):
                    self.logger.info(f"[STATS] [{percentage}%] {phase}: {message or ''}")
            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.error(f"Error sending websocket progress: {e}")

class ProgressReportingMixin:
    """Simplified progress reporting via callback."""
    def __init__(self):
        self.progress_callback: Optional[Callable] = None
    
    async def report_progress(self, phase: str, percentage: int):
        if self.progress_callback:
            try:
                await self.progress_callback(phase, percentage)
            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.error(f"Error reporting progress: {e}")
    
    def set_progress_callback(self, callback: Callable):
        self.progress_callback = callback

# =============================================================================
# 2. MISTAKE MEMORY
# =============================================================================

class MistakeMemoryMixin:
    """Mixin to add mistake memory capabilities to agents."""
    
    def __init__(self):
        self.progress_callback: Optional[Callable] = None
        self.mistake_memory_enabled = True
    
    async def check_past_mistakes(self, task_type: str, context: Dict = None) -> List[Dict]:
        from app.services.mistake_memory import mistake_memory
        if not self.mistake_memory_enabled: return []
        if not hasattr(self, 'agent_name') or not hasattr(self, 'project_id'): return []
        
        try:
            context_str = json.dumps(context or {})
            similar_mistakes_result = await mistake_memory.query_similar_mistakes(
                task_type=task_type,
                input_data=context_str,
                n_results=5,
                agent_name=self.agent_name
            )
            similar_mistakes = []
            if similar_mistakes_result and "metadatas" in similar_mistakes_result:
                for meta_list in similar_mistakes_result["metadatas"]:
                    if isinstance(meta_list, list): similar_mistakes.extend(meta_list)
                    else: similar_mistakes.append(meta_list)
            return similar_mistakes
        except Exception: return []
    
    async def record_failure(self, task_type: str, error: str, fix: str = None, context: Dict = None):
        from app.services.mistake_memory import mistake_memory
        if not self.mistake_memory_enabled: return
        if not hasattr(self, 'agent_name') or not hasattr(self, 'project_id'): return
        try:
            context_str = json.dumps(context or {})
            await mistake_memory.record_failure(
                task_type=task_type,
                input_data=context_str,
                error=error,
                fix=fix or "Retry attempted",
                agent_name=self.agent_name
            )
        except Exception: pass

    def incorporate_past_learnings(self, past_mistakes: List[Dict], prompt: str) -> str:
        if not past_mistakes: return prompt
        learnings_section = "\n\n**IMPORTANT - LEARN FROM PAST MISTAKES:**\n"
        for i, mistake in enumerate(past_mistakes[:5], 1):
            learnings_section += f"{i}. **Past Error**: {mistake.get('error', 'Unknown')[:200]}\n"
            learnings_section += f"   **How it was fixed**: {mistake.get('fix', 'No fix recorded')[:200]}\n\n"
        return prompt + learnings_section

# =============================================================================
# 3. PERMANENT MEMORY
# =============================================================================

class PermanentMemoryMixin:
    """Mixin to allow agents to store and recall general knowledge/patterns."""
    async def store_knowledge(self, topic: str, content: str, metadata: Dict = None):
        from app.services.agent_memory import agent_memory
        agent_name = getattr(self, "agent_name", "unknown")
        agent_memory.store_knowledge(agent_name, topic, content, metadata)

    async def recall_knowledge(self, query: str, n_results: int = 5) -> List[Dict]:
        from app.services.agent_memory import agent_memory
        agent_name = getattr(self, "agent_name", "unknown")
        return agent_memory.recall_knowledge(agent_name, query, n_results)

# =============================================================================
# 4. CONTEXT MANAGEMENT
# =============================================================================

class ContextManagementMixin:
    """Mixin to automatically manage context window size for agents."""
    async def manage_context(self, current_context: str, critical_state: Dict[str, Any], model: str = "default") -> str:
        from app.services.context_window_manager import context_window_manager
        agent_name = getattr(self, "agent_name", "unknown")
        project_id = getattr(self, "project_id", "unknown")
        status = context_window_manager.check_and_manage(agent_name, project_id, current_context, model)
        if status["action"] == "needs_summary":
            result = await context_window_manager.summarize_and_archive(agent_name, project_id, current_context, critical_state)
            return result["context"]
        return current_context

# =============================================================================
# 5. DECISION LEDGER
# =============================================================================

class DecisionLedgerMixin:
    """Mixin to record technical decisions."""
    def record_decision(self, decision_type: str, decision: str, reasoning: str, alternatives: List[str] = None, confidence: float = 0.0, context_snapshot: Dict = None) -> str:
        from app.services.decision_ledger import decision_ledger
        agent_name = getattr(self, "agent_name", "unknown")
        project_id = getattr(self, "project_id", "unknown")
        return decision_ledger.record_decision(project_id, agent_name, decision_type, decision, reasoning, alternatives, confidence, context_snapshot)

# =============================================================================
# 6. SEARCH CAPABILITY
# =============================================================================

class SearchCapableMixin:
    """Gives any agent web search capability."""
    async def search_for_solution(self, query: str, context: str = "") -> Dict[str, Any]:
        from app.agents.research_agent import ResearchAgent
        researcher = ResearchAgent(getattr(self, "project_id", "global"))
        return await researcher.find_error_solution(query, context)
    
    async def search_documentation(self, library: str, topic: str) -> Dict[str, Any]:
        from app.agents.research_agent import ResearchAgent
        researcher = ResearchAgent(getattr(self, "project_id", "global"))
        return await researcher.search_documentation(library, topic)

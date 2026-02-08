"""
Mistake Memory Mixin for Agents
================================

Provides mistake memory capabilities to all agents.
Agents can inherit this mixin to automatically get:
- Check past mistakes before execution
- Record failures for future learning
- Progress reporting callbacks
"""

import logging
from typing import Dict, List, Optional, Callable, Any
import json
from app.services.mistake_memory import mistake_memory
from app.services.agent_memory import agent_memory
from app.services.context_window_manager import context_window_manager
from app.services.decision_ledger import decision_ledger


class MistakeMemoryMixin:
    """
    Mixin to add mistake memory capabilities to agents.
    
    Usage:
        class MyAgent(MistakeMemoryMixin):
            def __init__(self, project_id: str):
                super().__init__()
                self.project_id = project_id
                self.agent_name = "my_agent"
                self.logger = logging.getLogger(f"{self.agent_name}.{project_id}")
    """
    
    def __init__(self):
        """Initialize mixin"""
        self.progress_callback: Optional[Callable] = None
        self.mistake_memory_enabled = True
    
    async def check_past_mistakes(
        self,
        task_type: str,
        context: Dict = None
    ) -> List[Dict]:
        """
        Check mistake memory for similar past failures.
        
        Args:
            task_type: Type of task being performed
            context: Additional context for similarity matching
        
        Returns:
            List of similar past mistakes
        """
        if not self.mistake_memory_enabled:
            return []
        
        if not hasattr(self, 'agent_name') or not hasattr(self, 'project_id'):
            self.logger.warning("Agent missing agent_name or project_id attributes")
            return []
        
        try:
            # Convert context to string for similarity search
            context_str = json.dumps(context or {})

            similar_mistakes_result = await mistake_memory.query_similar_mistakes(
                task_type=task_type,
                input_data=context_str,
                n_results=5,
                agent_name=self.agent_name
            )

            # Extract metadata from ChromaDB result
            similar_mistakes = []
            if similar_mistakes_result and "metadatas" in similar_mistakes_result:
                for meta_list in similar_mistakes_result["metadatas"]:
                    if isinstance(meta_list, list):
                        similar_mistakes.extend(meta_list)
                    else:
                        similar_mistakes.append(meta_list)
            
            if similar_mistakes:
                self.logger.info(
                    f"📚 Found {len(similar_mistakes)} similar past mistakes"
                )
                
                # Log the mistakes for awareness
                for mistake in similar_mistakes[:3]:  # Show top 3
                    self.logger.info(
                        f"  - Past error: {mistake.get('error', 'Unknown')[:100]}"
                    )
            
            return similar_mistakes
            
        except Exception as e:
            self.logger.error(f"Error checking mistake memory: {e}")
            return []
    
    async def record_failure(
        self,
        task_type: str,
        error: str,
        fix: str = None,
        context: Dict = None
    ):
        """
        Record a failure in mistake memory for future learning.
        
        Args:
            task_type: Type of task that failed
            error: Error message or description
            fix: How the error was fixed (if known)
            context: Additional context about the failure
        """
        if not self.mistake_memory_enabled:
            return
        
        if not hasattr(self, 'agent_name') or not hasattr(self, 'project_id'):
            self.logger.warning("Agent missing agent_name or project_id attributes")
            return
        
        try:
            # Convert context to string
            context_str = json.dumps(context or {})

            await mistake_memory.record_failure(
                task_type=task_type,
                input_data=context_str,
                error=error,
                fix=fix or "Retry attempted",
                agent_name=self.agent_name
            )
            
            self.logger.info(f"📝 Recorded failure in mistake memory")
            
        except Exception as e:
            self.logger.error(f"Error recording failure: {e}")
    
    async def report_progress(self, phase: str, percentage: int):
        """
        Report progress to callback (usually Arjun).
        
        Args:
            phase: Current phase name
            percentage: Progress percentage (0-100)
        """
        if self.progress_callback:
            try:
                await self.progress_callback(phase, percentage)
            except Exception as e:
                self.logger.error(f"Error reporting progress: {e}")
    
    def set_progress_callback(self, callback: Callable):
        """
        Set progress callback function.
        
        Args:
            callback: Async function(phase: str, percentage: int)
        """
        self.progress_callback = callback
    
    def incorporate_past_learnings(
        self,
        past_mistakes: List[Dict],
        prompt: str
    ) -> str:
        """
        Incorporate past learnings into a prompt.
        
        Args:
            past_mistakes: List of past mistakes from check_past_mistakes()
            prompt: Original prompt
        
        Returns:
            Enhanced prompt with learnings
        """
        if not past_mistakes:
            return prompt
        
        learnings_section = "\n\n**IMPORTANT - LEARN FROM PAST MISTAKES:**\n"
        learnings_section += "The following issues occurred in similar tasks before. Avoid them:\n\n"
        
        for i, mistake in enumerate(past_mistakes[:5], 1):  # Top 5
            error = mistake.get('error', 'Unknown error')
            fix = mistake.get('fix', 'No fix recorded')
            
            learnings_section += f"{i}. **Past Error**: {error[:200]}\n"
            learnings_section += f"   **How it was fixed**: {fix[:200]}\n\n"
        
        # Insert learnings near the beginning of the prompt
        return prompt + learnings_section


class ProgressReportingMixin:
    """
    Mixin to add progress reporting to agents.
    Simpler version if mistake memory is not needed.
    """
    
    def __init__(self):
        self.progress_callback: Optional[Callable] = None
    
    async def report_progress(self, phase: str, percentage: int):
        """Report progress to callback"""
        if self.progress_callback:
            try:
                await self.progress_callback(phase, percentage)
            except Exception as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"Error reporting progress: {e}")
    
    def set_progress_callback(self, callback: Callable):
        """Set progress callback"""
        self.progress_callback = callback

    def incorporate_past_learnings(
        self,
        past_mistakes: List[Dict],
        prompt: str
    ) -> str:
        """
        Identical to MistakeMemoryMixin.incorporate_past_learnings.
        Implemented here to allow standalone use.
        """
        if not past_mistakes:
            return prompt
        
        learnings_section = "\n\n**IMPORTANT - LEARN FROM PAST MISTAKES:**\n"
        learnings_section += "The following issues occurred in similar tasks before. Avoid them:\n\n"
        
        for i, mistake in enumerate(past_mistakes[:5], 1):
            error = mistake.get('error', 'Unknown error')
            fix = mistake.get('fix', 'No fix recorded')
            learnings_section += f"{i}. **Past Error**: {error[:200]}\n"
            learnings_section += f"   **How it was fixed**: {fix[:200]}\n\n"
        
        return prompt + learnings_section


class PermanentMemoryMixin:
    """
    Mixin to allow agents to store and recall general knowledge/patterns.
    """
    
    async def store_knowledge(self, topic: str, content: str, metadata: Dict = None):
        """Store permanent knowledge for this agent."""
        agent_name = getattr(self, "agent_name", "unknown")
        agent_memory.store_knowledge(agent_name, topic, content, metadata)
        if hasattr(self, "logger"):
            self.logger.info(f"🧠 Permanent knowledge stored for topic: {topic}")

    async def recall_knowledge(self, query: str, n_results: int = 5) -> List[Dict]:
        """Recall permanent knowledge relevant to a query."""
        agent_name = getattr(self, "agent_name", "unknown")
        return agent_memory.recall_knowledge(agent_name, query, n_results)


class ContextManagementMixin:
    """
    Mixin to automatically manage context window size for agents.
    """
    
    async def manage_context(
        self,
        current_context: str,
        critical_state: Dict[str, Any],
        model: str = "default"
    ) -> str:
        """
        Check context size and auto-summarize if needed.
        Returns the (potentially new) context to use.
        """
        agent_name = getattr(self, "agent_name", "unknown")
        project_id = getattr(self, "project_id", "unknown")
        
        status = context_window_manager.check_and_manage(
            agent_name=agent_name,
            project_id=project_id,
            current_context=current_context,
            model=model
        )
        
        if status["action"] == "needs_summary":
            result = await context_window_manager.summarize_and_archive(
                agent_name=agent_name,
                project_id=project_id,
                current_context=current_context,
                critical_state=critical_state
            )
            return result["context"]
        
        return current_context


class DecisionLedgerMixin:
    """
    Mixin to allow agents to record their technical decisions and reasoning.
    """
    
    def record_decision(
        self,
        decision_type: str,
        decision: str,
        reasoning: str,
        alternatives: List[str] = None,
        confidence: float = 0.0,
        context_snapshot: Dict = None
    ) -> str:
        """Record a decision in the global ledger."""
        agent_name = getattr(self, "agent_name", "unknown")
        project_id = getattr(self, "project_id", "unknown")
        
        return decision_ledger.record_decision(
            project_id=project_id,
            agent_name=agent_name,
            decision_type=decision_type,
            decision=decision,
            reasoning=reasoning,
            alternatives=alternatives,
            confidence=confidence,
            context_snapshot=context_snapshot
        )


class SearchCapableMixin:
    """Gives any agent web search capability via ResearchAgent."""
    
    async def search_for_solution(self, query: str, context: str = "") -> Dict[str, Any]:
        """Search web for solution to a problem."""
        from app.agents.research_agent import ResearchAgent
        # Using self.project_id if available, fallback to "global"
        project_id = getattr(self, "project_id", "global")
        researcher = ResearchAgent(project_id)
        return await researcher.find_error_solution(
            error_message=query,
            code_context=context
        )
    
    async def search_documentation(self, library: str, topic: str) -> Dict[str, Any]:
        """Search for library documentation."""
        from app.agents.research_agent import ResearchAgent
        project_id = getattr(self, "project_id", "global")
        researcher = ResearchAgent(project_id)
        return await researcher.search_documentation(
            library=library,
            topic=topic
        )
    
    async def search_before_generating(self, tech_stack: List[str]) -> Dict[str, str]:
        """Proactive search: check for latest versions, breaking changes."""
        from app.agents.research_agent import ResearchAgent
        project_id = getattr(self, "project_id", "global")
        researcher = ResearchAgent(project_id)
        
        findings = {}
        for tech in tech_stack[:5]:  # Limit to avoid cost explosion
            result = await researcher.execute({
                "task_type": "best_practice",
                "query": f"Latest {tech} best practices 2026",
                "context": {"topic": f"Building production app with {tech}"}
            })
            findings[tech] = result.get("summary", "")
        
        return findings

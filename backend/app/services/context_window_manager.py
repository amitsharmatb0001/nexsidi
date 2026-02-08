
import json
import logging
import tiktoken
from typing import Dict, Any, Optional
from app.services.context_engine import context_engine
from app.services.ai_router import ai_router, TaskComplexity


class ContextWindowManager:
    """
    Monitors and manages context window size per agent.
    
    When context reaches 80% of limit:
    1. Summarize current context
    2. Archive full context to storage
    3. Create fresh context with summary + critical state
    4. Agent continues seamlessly
    """
    
    # Token limits per model
    MODEL_LIMITS = {
        "gemini-flash": 128000,
        "gemini-pro": 128000,
        "claude-sonnet": 200000,
        "claude-opus": 200000,
        "default": 100000
    }
    
    THRESHOLD_PERCENT = 80  # Trigger at 80% capacity
    
    def __init__(self):
        self.logger = logging.getLogger("context_window_manager")
        self.encoder = None
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.logger.warning("tiktoken not available, using word-based estimation")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self.encoder:
            return len(self.encoder.encode(text))
        # Fallback: ~4 chars per token
        return len(text) // 4
    
    def check_and_manage(
        self,
        agent_name: str,
        project_id: str,
        current_context: str,
        model: str = "default"
    ) -> Dict[str, Any]:
        """
        Check if context needs management.
        
        Returns:
            {
                "action": "none" | "summarized",
                "context": <potentially summarized context>,
                "archived": bool,
                "session_id": <archive reference>
            }
        """
        token_count = self.count_tokens(current_context)
        limit = self.MODEL_LIMITS.get(model, self.MODEL_LIMITS["default"])
        threshold = int(limit * self.THRESHOLD_PERCENT / 100)
        
        if token_count < threshold:
            return {
                "action": "none",
                "context": current_context,
                "tokens": token_count,
                "limit": limit,
                "usage_percent": round(token_count / limit * 100, 1)
            }
        
        self.logger.warning(
            f"⚠️ Context window at {token_count}/{limit} tokens "
            f"({token_count/limit*100:.0f}%) for {agent_name}. "
            f"Triggering auto-summarize."
        )
        
        return {
            "action": "needs_summary",
            "tokens": token_count,
            "limit": limit
        }
    
    async def summarize_and_archive(
        self,
        agent_name: str,
        project_id: str,
        current_context: str,
        critical_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Summarize context, archive full version, return fresh context.
        """
        import time
        
        # 1. Archive full context
        session_id = f"{agent_name}_{int(time.time())}"
        context_engine.store_context(
            project_id,
            f"archived_session_{session_id}",
            {
                "agent": agent_name,
                "full_context": current_context,
                "archived_at": time.time(),
                "critical_state": critical_state
            }
        )
        
        # 2. Summarize using AI
        summary_prompt = f"""Summarize the following context for {agent_name} agent.
Keep ALL critical information: decisions made, current state, errors encountered,
files generated, pending tasks. Remove verbose logs and repetitive content.

CONTEXT TO SUMMARIZE:
{current_context[:50000]}  

Return a concise summary (max 2000 words) preserving all actionable information."""
        
        response = await ai_router.generate(
            messages=[{"role": "user", "content": summary_prompt}],
            task_type="summarization",
            complexity=TaskComplexity.SIMPLE,
            max_tokens=3000,
            agent_name="context_manager"
        )
        
        # 3. Build fresh context
        fresh_context = f"""=== CONTEXT RESUMED (Session archived: {session_id}) ===

SUMMARY OF PREVIOUS CONTEXT:
{response.content}

CRITICAL STATE:
{json.dumps(critical_state, indent=2)}

=== CONTINUE FROM HERE ===
"""
        
        self.logger.info(
            f"✅ Context summarized for {agent_name}: "
            f"{self.count_tokens(current_context)} → {self.count_tokens(fresh_context)} tokens"
        )
        
        return {
            "action": "summarized",
            "context": fresh_context,
            "session_id": session_id,
            "tokens_before": self.count_tokens(current_context),
            "tokens_after": self.count_tokens(fresh_context),
            "archived": True
        }
    
    def restore_archived_session(
        self, 
        project_id: str, 
        session_id: str
    ) -> Optional[Dict]:
        """Restore a previously archived session (for debugging/support)."""
        return context_engine.get_context(
            project_id,
            f"archived_session_{session_id}"
        )


context_window_manager = ContextWindowManager()

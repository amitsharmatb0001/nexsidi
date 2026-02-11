# File: app/services/mistake_memory.py

import logging
from typing import List, Dict, Any, Optional
from app.services.agent_memory import agent_memory

class MistakeMemory:
    """
    Learn from past failures (patent requirement).
    Delegates to AgentPermanentMemory for per-agent persistent storage.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("mistake_memory")
    
    def record_failure(self, task_type: str, input_data: str, 
                      error: str, fix: str, agent_name: str = "unknown"):
        """
        Store mistake in per-agent permanent memory.
        """
        self.logger.info(f"[SAVE] Recording failure for {agent_name} - {task_type}...")
        try:
            agent_memory.store_mistake(
                agent_name=agent_name,
                task_type=task_type,
                input_data=input_data,
                error=error,
                fix=fix or "Retry attempted"
            )
        except Exception as e:
            self.logger.error(f"[ERROR] Failed to record failure: {e}")
    
    def query_similar_mistakes(self, task_type: str, 
                               input_data: str, 
                               n_results: int = 5,
                               agent_name: str = "unknown") -> Dict[str, Any]:
        """
        Check if similar mistake happened before for this agent.
        """
        try:
            return agent_memory.recall_mistakes(
                agent_name=agent_name,
                task_type=task_type,
                query=input_data,
                n_results=n_results
            )
        except Exception as e:
            self.logger.error(f"[ERROR] Failed to query mistakes: {e}")
            return {"ids": [], "metadatas": [], "documents": []}
    
    def modify_prompt_with_lessons(self, base_prompt: str, 
                                   task_type: str,
                                   current_input: str = "",
                                   agent_name: str = "unknown") -> str:
        """
        Inject past mistakes into prompt to avoid repetition.
        """
        similar = self.query_similar_mistakes(task_type, current_input, agent_name=agent_name)
        
        lessons_list = []
        if similar and "metadatas" in similar and similar["metadatas"]:
            for meta_list in similar["metadatas"]:
                for m in meta_list:
                    if isinstance(m, dict) and "error" in m and "fix" in m:
                        lessons_list.append(f"- Avoid: {m['error']} → Use: {m['fix']}")
        
        if lessons_list:
            lessons_text = "\n".join(lessons_list)
            self.logger.info(f"[AI] Injecting {len(lessons_list)} lessons into prompt for {agent_name}:{task_type}")
            return f"{base_prompt}\n\n### LEARNED FROM PAST MISTAKES (DO NOT REPEAT):\n{lessons_text}"
        
        return base_prompt

# Global instance
mistake_memory = MistakeMemory()

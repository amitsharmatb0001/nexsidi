"""
RESEARCH AGENT - Web Search & Documentation Learning
=====================================================
Location: app/agents/research_agent.py

Purpose: Search the web for documentation, learn new frameworks, find solutions.
This fills Patent Gap #4 - "Web Search/Research capability"

Use cases:
- Find documentation for new libraries
- Learn latest API changes
- Search for solutions to errors
- Research best practices

Model: Gemini Pro (web search capable)
"""

import json
import logging
from typing import Dict, Any, List
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.agents.mixins import MistakeMemoryMixin


class ResearchAgent(MistakeMemoryMixin):
    """
    Research Agent - Web search and learning.
    
    Capabilities:
    - Search documentation
    - Find code examples
    - Learn new frameworks
    - Research error solutions
    """
    
    def __init__(self, project_id: str):
        """Initialize Research Agent"""
        super().__init__()
        
        self.project_id = project_id
        self.agent_name = "research"
        self.ai_router = ai_router
        self.logger = logging.getLogger("agent.research")
        
        # Statistics
        self.total_searches = 0
        self.total_cost = 0.0
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform research task.
        
        Args:
            input_data: {
                "query": "How to implement OAuth with FastAPI",
                "task_type": "documentation|error_solution|best_practice",
                "context": {...}  # Optional context
            }
        
        Returns:
            {
                "findings": [...],
                "sources": [...],
                "summary": "..."
            }
        """
        try:
            query = input_data.get("query")
            task_type = input_data.get("task_type", "documentation")
            context = input_data.get("context", {})
            
            if not query:
                raise ValueError("Query is required")
            
            self.logger.info(f"🔍 Researching: {query}")
            
            # Perform web search
            findings = await self._web_search(query, task_type, context)
            
            self.total_searches += 1
            
            return {
                "status": "success",
                "query": query,
                "findings": findings.get("findings", []),
                "sources": findings.get("sources", []),
                "summary": findings.get("summary", ""),
                "cost": self.total_cost
            }
            
        except Exception as e:
            self.logger.error(f"❌ Research failed: {e}")
            raise
    
    async def _web_search(
        self,
        query: str,
        task_type: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Perform web search using AI.
        
        In production: Use actual web search API (Google, Bing, etc.)
        For now: Use AI with grounding/web search capability
        """
        prompt = prompt_engine.get_prompt(
            "research",
            "web_search",
            context={
                "query": query,
                "task_type": task_type,
                "context": json.dumps(context, indent=2)
            }
        )
        
        # Use Gemini with grounding (web search)
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="research",
            complexity=TaskComplexity.MEDIUM,  # Fixed: was COMPLEX
            max_tokens=2000  # Fixed: was 4000
            # Removed: enable_web_search=True (not supported by AIRouter)
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            # Fallback if not JSON
            return {
                "findings": [response.content],
                "sources": [],
                "summary": response.content[:500]
            }
    
    async def search_documentation(
        self,
        library: str,
        topic: str
    ) -> Dict[str, Any]:
        """
        Search for specific library documentation.
        
        Example:
            await research.search_documentation("FastAPI", "OAuth2")
        """
        query = f"{library} {topic} documentation examples"
        
        return await self.execute({
            "query": query,
            "task_type": "documentation"
        })
    
    async def find_error_solution(
        self,
        error_message: str,
        code_context: str = None
    ) -> Dict[str, Any]:
        """
        Search for solutions to an error.
        
        Example:
            await research.find_error_solution(
                "ModuleNotFoundError: No module named 'pydantic'",
                code_context="from pydantic import BaseModel"
            )
        """
        query = f"How to fix: {error_message}"
        
        context = {}
        if code_context:
            context["code"] = code_context
        
        return await self.execute({
            "query": query,
            "task_type": "error_solution",
            "context": context
        })
    
    async def research_best_practices(
        self,
        topic: str,
        framework: str = None
    ) -> Dict[str, Any]:
        """
        Research best practices for a topic.
        
        Example:
            await research.research_best_practices(
                "authentication",
                framework="FastAPI"
            )
        """
        query = f"Best practices for {topic}"
        if framework:
            query += f" in {framework}"
        
        return await self.execute({
            "query": query,
            "task_type": "best_practice"
        })
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get research statistics"""
        return {
            "total_searches": self.total_searches,
            "total_cost": self.total_cost
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        research = ResearchAgent(project_id="test-research-001")
        
        # Test documentation search
        result = await research.search_documentation(
            "FastAPI",
            "WebSocket implementation"
        )
        
        print("Documentation Search:")
        print(json.dumps(result, indent=2))
        
        # Test error solution
        error_result = await research.find_error_solution(
            "CORS policy: No 'Access-Control-Allow-Origin' header"
        )
        
        print("\nError Solution:")
        print(json.dumps(error_result, indent=2))
        
        print(f"\nStatistics: {research.get_statistics()}")
    
    asyncio.run(test())

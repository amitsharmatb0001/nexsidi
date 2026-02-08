import json
import logging
from typing import Dict, Any
from app.services.ai_router import ai_router, TaskComplexity
from app.services.context_engine import context_engine
from app.services.prompt_engine import prompt_engine

class DocumentGenerator:
    """
    Service to generate project documentation by aggregating context from all agents.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("document_generator")
        self.logger.setLevel(logging.INFO)

    async def generate_sdd(self, project_id: str) -> str:
        """
        Generate a full Software Design Document (SDD) for a project.
        
        Args:
            project_id: The unique ID of the project
            
        Returns:
            String containing the SDD in markdown format
        """
        try:
            self.logger.info(f"📄 Generating SDD for project: {project_id}")
            
            # 1. Get ALL context from ContextEngine
            context = context_engine.get_full_context(project_id)
            if not context:
                self.logger.warning(f"⚠️ No context found for project {project_id}")
                return "# SDD: No context available for this project."

            # 2. Get prompt from PromptEngine
            prompt = prompt_engine.get_prompt(
                "document_generator",
                "sdd_generation",
                context={
                    "project_context": context
                }
            )
            
            # 3. Call AI Router
            response = await ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="architecture",
                complexity=TaskComplexity.COMPLEX
            )
            
            self.logger.info(f"✅ SDD generated successfully for {project_id}")
            return response.content
            
        except Exception as e:
            self.logger.error(f"❌ SDD generation failed: {e}")
            return f"# Error generating SDD\n\n{str(e)}"

# Global instance
document_generator = DocumentGenerator()

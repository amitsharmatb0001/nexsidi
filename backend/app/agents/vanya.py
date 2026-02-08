"""
VANYA - UI/UX DESIGN AGENT
===========================
Location: app/agents/vanya.py

Purpose: Generate UI/UX mockups and design tokens BEFORE code generation.
This is the "In-House Figma" from the patent - creates visual mockups first.

Flow:
1. Saanvi creates requirements
2. Vanya generates UI mockups (JSON design tokens)
3. Aanya uses mockups to generate actual frontend code

Model: Gemini Pro (vision capable for design)
"""

import json
import logging
from typing import Dict, Any, List
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.services.workspace_manager import workspace_manager
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin
)


class Vanya(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin):
    """
    UI/UX Design Agent - Creates mockups and design systems.
    
    Standalone V2 architecture - no inheritance.
    Uses AI Router directly.
    
    Output: Design tokens (colors, typography, spacing, components)
    """
    
    def __init__(self, project_id: str, workspace: Dict[str, str]):
        """Initialize Vanya for a project"""
        super().__init__()
        
        self.project_id = project_id
        self.agent_name = "vanya"
        self.ai_router = ai_router
        self.logger = logging.getLogger("agent.vanya")
        self.workspace = workspace
        
        # Statistics
        self.mockups_generated = 0
        self.total_cost = 0.0
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate UI/UX mockups based on requirements.
        
        Args:
            input_data: {
                "requirements": {...},
                "target_audience": "general|business|creative",
                "style_preference": "modern|minimalist|bold|playful"
            }
        
        Returns:
            {
                "design_system": {...},
                "page_mockups": [...],
                "component_library": {...}
            }
        """
        try:
            self.logger.info("🎨 Starting UI/UX design generation...")
            
            requirements = input_data.get("requirements", {})
            style = input_data.get("style_preference", "modern")
            audience = input_data.get("target_audience", "general")
            
            # Generate design system
            design_system = await self._generate_design_system(
                requirements, style, audience
            )
            
            # Generate page mockups
            page_mockups = await self._generate_page_mockups(
                requirements, design_system
            )
            
            # Generate component library
            components = await self._generate_component_library(
                requirements, design_system
            )
            
            self.mockups_generated += 1
            
            result = {
                "status": "success",
                "design_system": design_system,
                "page_mockups": page_mockups,
                "component_library": components,
                "cost": self.total_cost
            }
            
            self.logger.info(
                f"✅ UI/UX design complete: {len(page_mockups)} pages, "
                f"₹{self.total_cost:.2f}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ UI/UX design failed: {e}")
            await self.record_failure(
                task_type="ui_ux_design",
                error=str(e),
                context={"input_data": input_data}
            )
            raise
    
    async def _generate_design_system(
        self,
        requirements: Dict[str, Any],
        style: str,
        audience: str
    ) -> Dict[str, Any]:
        """
        Generate design system (colors, typography, spacing).
        
        Returns design tokens that Aanya will use for code generation.
        """
        prompt = prompt_engine.get_prompt(
            "vanya",
            "design_system",
            context={
                "requirements": json.dumps(requirements, indent=2),
                "style": style,
                "audience": audience
            }
        )

        # Check past mistakes for design system
        past_mistakes = await self.check_past_mistakes(
            task_type="design_system",
            context={"style": style, "audience": audience}
        )
        
        if past_mistakes:
            prompt = self.incorporate_past_learnings(past_mistakes, prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past learnings for design system")
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="ui_design",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=4000
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            # Return default design system
            return self._default_design_system()
    
    async def _generate_page_mockups(
        self,
        requirements: Dict[str, Any],
        design_system: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Generate mockups for each page.
        
        Each mockup includes:
        - Layout structure (header, content, footer)
        - Component placement
        - Content hierarchy
        """
        prompt = prompt_engine.get_prompt(
            "vanya",
            "page_mockups",
            context={
                "requirements": json.dumps(requirements, indent=2),
                "design_system": json.dumps(design_system, indent=2)
            }
        )

        # Check past mistakes for page mockups
        past_mistakes = await self.check_past_mistakes(
            task_type="page_mockups",
            context={"page_count": len(requirements.get("pages", []))}
        )
        
        if past_mistakes:
            prompt = self.incorporate_past_learnings(past_mistakes, prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past learnings for mockups")
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="ui_design",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=6000
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            result = json.loads(response.content)
            return result.get("pages", [])
        except json.JSONDecodeError:
            return []
    
    async def _generate_component_library(
        self,
        requirements: Dict[str, Any],
        design_system: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate reusable component specifications.
        
        Components: buttons, cards, forms, navigation, etc.
        """
        prompt = prompt_engine.get_prompt(
            "vanya",
            "component_library",
            context={
                "requirements": json.dumps(requirements, indent=2),
                "design_system": json.dumps(design_system, indent=2)
            }
        )

        # Check past mistakes for component library
        past_mistakes = await self.check_past_mistakes(
            task_type="component_library",
            context={"req_count": len(requirements)}
        )
        
        if past_mistakes:
            prompt = self.incorporate_past_learnings(past_mistakes, prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past learnings for components")
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="ui_design",
            complexity=TaskComplexity.SIMPLE,
            max_tokens=3000
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"components": []}
    
    def _default_design_system(self) -> Dict[str, Any]:
        """Fallback design system if AI generation fails"""
        return {
            "colors": {
                "primary": "#3B82F6",
                "secondary": "#8B5CF6",
                "accent": "#F59E0B",
                "background": "#FFFFFF",
                "surface": "#F3F4F6",
                "text": "#111827",
                "text_secondary": "#6B7280"
            },
            "typography": {
                "font_family": "Inter, system-ui, sans-serif",
                "heading_sizes": {
                    "h1": "36px",
                    "h2": "30px",
                    "h3": "24px",
                    "h4": "20px"
                },
                "body_size": "16px"
            },
            "spacing": {
                "xs": "4px",
                "sm": "8px",
                "md": "16px",
                "lg": "24px",
                "xl": "32px"
            },
            "border_radius": {
                "sm": "4px",
                "md": "8px",
                "lg": "12px",
                "full": "9999px"
            }
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get generation statistics"""
        return {
            "mockups_generated": self.mockups_generated,
            "total_cost": self.total_cost
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        vanya = Vanya(
            project_id="test-ui-001",
            workspace={"code_dir": "/tmp/test"}
        )
        
        input_data = {
            "requirements": {
                "app_type": "restaurant_website",
                "features": ["menu", "reservations", "contact"]
            },
            "style_preference": "modern",
            "target_audience": "general"
        }
        
        result = await vanya.execute(input_data)
        print(json.dumps(result, indent=2))
        print(f"\nStatistics: {vanya.get_statistics()}")
    
    asyncio.run(test())

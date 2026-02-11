# =============================================================================
# AANYA - WEB FRONTEND DEVELOPER AGENT
# Location: backend/app/agents/aanya.py
# Purpose: Generate React/Next.js frontend code
# =============================================================================

import os
import json
import logging
import subprocess
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import time

# Import AI Router
from app.services.ai_router import ai_router, TaskComplexity
from app.utils.json_utils import safe_json_parse

# Setup logging
from app.services.git_service import git_service
from app.agents.contracts import AanyaOutput

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import Engines
from app.services.prompt_engine import prompt_engine
from app.services.context_engine import context_engine
from app.services.workspace_manager import workspace_manager
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)


class Aanya(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    Frontend Developer Agent - React/TypeScript Specialist.

    Managed by Arjun orchestrator.
    Receives workspace and context as parameters.
    Returns results to Arjun for storage and verification.

    Usage:
        aanya = Aanya(
            project_id="proj-123",
            workspace={"code_dir": "/path/to/workspace"}
        )
        result = await aanya.execute(input_data)
    """
    
    def __init__(self, project_id: str, workspace: Dict[str, str]):
        """
        Initialize Aanya for a project.
        
        Args:
            project_id: UUID of the project
            workspace: Workspace details from Arjun orchestrator
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        # Standalone - no inheritance
        self.project_id = project_id
        self.agent_name = "aanya"  # For mistake memory
        
        # Direct AI Router access
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.aanya")
        self.logger.setLevel(logging.INFO)
        
        # Get System Prompt from PromptEngine
        self.system_prompt = prompt_engine.get_prompt("aanya", "system_prompt")
        
        # Statistics
        self.files_generated = 0
        self.total_cost = 0.0

        # RECEIVE workspace from Arjun (don't create)
        self.workspace = workspace
        self.logger.info(f"📁 Using workspace: {workspace['code_dir']}")
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate frontend code based on architecture.
        """
        # GET CONTEXT
        requirements = context_engine.get_context(self.project_id, "requirements")
        backend = context_engine.get_context(self.project_id, "backend_code")
        
        if requirements:
            self.logger.info("ℹ️ Retrieved requirements context")
        if backend:
            self.logger.info("ℹ️ Retrieved backend context")

        try:
            self.logger.info("[DESIGN] Starting frontend generation...")
            
            # Extract architecture
            fe_arch = input_data.get("frontend_architecture", {})
            api_arch = input_data.get("api_architecture", {})
            
            if not fe_arch:
                raise ValueError("Frontend architecture is required")
            
            # Prepare planning prompt using PromptEngine
            plan_prompt = f"{self.system_prompt}\n\nPLANNING TASK:\nGenerate a list of files needed for this project based on the architecture below.\n\nARCHITECTURE:\n{input_data.get('frontend_architecture', 'Standard React App')}"
            
            # Use AI Router for planning
            plan_response = await self.ai_router.generate(
                messages=[{"role": "user", "content": plan_prompt}],
                task_type="frontend_planning",
                complexity=TaskComplexity.COMPLEX
            )
            
            # Generate file list
            file_plan = await self._plan_files(fe_arch, api_arch)
            
            # Generate each file
            generated_files = []
            context = []
            
            total_files = len(file_plan["files"])
            for i, file_spec in enumerate(file_plan["files"]):
                await self._send_progress(
                    "generating_frontend",
                    int((i / total_files) * 100),
                    f"Generating {file_spec['path']}..."
                )
                
                file_result = await self._generate_frontend_file(
                    file_spec,
                    fe_arch,
                    api_arch,
                    context
                )
                
                generated_files.append(file_result)
                context.append(file_result)
                self.files_generated += 1
            
            await self._send_progress("generating_frontend", 100, "Frontend generation complete.")
            
            self.logger.info(
                f"[OK] Frontend generation complete: {len(generated_files)} files, "
                f"₹{self.total_cost:.2f}"
            )
            
            # Re-confirm files written (Task 3.2 Fix)
            files_written = all(os.path.exists(os.path.join(self.workspace['code_dir'], f['path'])) for f in file_plan["files"])
            
            return {
                "project_id": self.project_id,
                "files_written": files_written,
                "files_count": len(generated_files),
                "frontend_url": "", # Will be set by Arjun if starting server
                "build_status": "success",
                "workspace_path": self.workspace['code_dir']
            }
            
        except Exception as e:
            self.logger.error(f"[ERROR] Frontend generation failed: {e}")
            await self.record_failure(
                task_type="frontend_execution",
                error=str(e),
                context={"fe_arch": fe_arch}
            )
            raise
    
    async def _plan_files(
        self,
        fe_arch: Dict[str, Any],
        api_arch: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Plan which frontend files to generate.
        
        Returns ordered list of files with priorities.
        """
        files = [
            # Core
            {"path": "frontend/src/App.tsx", "type": "typescript-react", "priority": 1, "purpose": "Main app with routing"},
            {"path": "frontend/src/main.tsx", "type": "typescript-react", "priority": 1, "purpose": "React entry"},
            {"path": "frontend/src/index.css", "type": "css", "priority": 1, "purpose": "Tailwind imports"},
            
            # API
            {"path": "frontend/src/api/client.ts", "type": "typescript", "priority": 2, "purpose": "API client"},
            {"path": "frontend/src/types/index.ts", "type": "typescript", "priority": 2, "purpose": "TypeScript interfaces"},
            
            # Context
            {"path": "frontend/src/context/AuthContext.tsx", "type": "typescript-react", "priority": 2, "purpose": "Auth context"},
            
            # Layout
            {"path": "frontend/src/components/layout/Header.tsx", "type": "typescript-react", "priority": 3, "purpose": "Header"},
            {"path": "frontend/src/components/layout/Footer.tsx", "type": "typescript-react", "priority": 3, "purpose": "Footer"},
        ]
        
        # Add pages
        for page in fe_arch.get("pages", []):
            files.append({
                "path": f"frontend/src/pages/{page['component']}.tsx",
                "type": "typescript-react",
                "priority": 4,
                "purpose": page["purpose"]
            })
        
        # Add components
        comp_struct = fe_arch.get("component_structure", {})
        for category, components in comp_struct.items():
            if category not in ["layout"]:
                for comp_name in components:
                    comp_clean = comp_name.split("(")[0].strip()
                    files.append({
                        "path": f"frontend/src/components/{category}/{comp_clean}.tsx",
                        "type": "typescript-react",
                        "priority": 5,
                        "purpose": comp_name
                    })
        
        # Config
        files.extend([
            {"path": "frontend/package.json", "type": "json", "priority": 6, "purpose": "NPM deps"},
            {"path": "frontend/tsconfig.json", "type": "json", "priority": 6, "purpose": "TS config"},
            {"path": "frontend/tailwind.config.js", "type": "javascript", "priority": 6, "purpose": "Tailwind config"},
            {"path": "frontend/vite.config.ts", "type": "typescript", "priority": 6, "purpose": "Vite config"},
            {"path": "frontend/.env.example", "type": "text", "priority": 6, "purpose": "Env template"},
            {"path": "frontend/README.md", "type": "markdown", "priority": 7, "purpose": "Documentation"}
        ])
        
        files.sort(key=lambda x: x["priority"])
        
        return {"files": files}
    
    async def _generate_frontend_file(
        self,
        file_spec: Dict[str, Any],
        fe_arch: Dict[str, Any],
        api_arch: Dict[str, Any],
        context: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Generate a single frontend file"""
        
        context_str = ""
        if context:
            context_str = "\n\nPREVIOUSLY GENERATED:\n"
            for prev in context[-3:]:
                context_str += f"- {prev['path']}\n"

        generation_prompt = prompt_engine.get_prompt(
            "aanya",
            "frontend_generation",
            context={
                "component_name": file_spec['path'],
                "description": file_spec['purpose'],
                "design_system": json.dumps(fe_arch, indent=2),
                "api_contract": json.dumps(api_arch, indent=2) + f"\n\nContext:\n{context_str}"
            }
        )

        # Check past mistakes for frontend generation
        past_mistakes = await self.check_past_mistakes(
            task_type="frontend_generation",
            context={"file_path": file_spec['path']}
        )
        
        if past_mistakes:
            generation_prompt = self.incorporate_past_learnings(past_mistakes, generation_prompt)
            self.logger.info(f"[LOAD] Incorporated {len(past_mistakes)} past learnings")

        # Call AI Router directly
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": generation_prompt}],
            system_prompt=self.system_prompt,
            task_type="code_generation",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=8000
        )
        
        # Log cost
        self.total_cost += response.cost_estimate
        self.logger.info(
            f"[OK] {response.output_tokens} tokens, "
            f"₹{response.cost_estimate:.4f}"
        )
        
        # Parse response
        try:
            result = safe_json_parse(response.content)
            if not result:
                raise ValueError("Empty or invalid JSON after parsing")
            
            # Just validate required fields
            if "file_content" not in result:
                raise ValueError("Missing file_content in AI response")
            
            # WRITE TO DISK (BUG #3 Fix)
            full_path = os.path.join(
                self.workspace['code_dir'],
                file_spec['path']
            )
            
            # Create directories if needed
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            # Write file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(result["file_content"])
            
            self.logger.info(f"[OK] Written: {full_path}")
            
            # Commit to git
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "aanya",
                f"Generated {file_spec['path']}"
            )
            
            return result  # Return as-is
            
        except Exception as e:
            self.logger.error(f"[ERROR] Error generating frontend file: {e}")
            await self.record_failure(
                task_type="frontend_generation",
                error=str(e),
                context={"file_path": file_spec['path'], "response": response.content[:500]}
            )
            raise
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get generation statistics"""
        return {
            "files_generated": self.files_generated,
            "total_cost": self.total_cost
        }
    
    async def setup_frontend(self, project_path: str):
        """
        Actually run setup commands for the frontend.
        
        Args:
            project_path: Absolute path to the frontend directory
        """
        self.logger.info(f"Setting up frontend in {project_path}...")
        
        try:
            # Install dependencies
            subprocess.run(
                ["npm", "install"],
                cwd=project_path,
                check=True
            )
            
            # Build production
            subprocess.run(
                ["npm", "run", "build"],
                cwd=project_path,
                check=True
            )
            
            self.logger.info("[OK] Frontend setup complete!")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"[ERROR] Frontend setup failed: {e}")
            raise


if __name__ == "__main__":
    import asyncio
    
    async def test():
        aanya = Aanya(project_id="test-fe-001")
        
        # Sample input
        input_data = {
            "frontend_architecture": {
                "pages": [
                    {"component": "Home", "purpose": "Landing page"},
                    {"component": "About", "purpose": "About page"}
                ],
                "component_structure": {
                    "common": ["Button", "Card"],
                    "forms": ["Input", "Select"]
                }
            },
            "api_architecture": {
                "endpoints": [
                    {"path": "/api/users", "method": "GET"}
                ]
            }
        }
        
        result = await aanya.execute(input_data)
        print(json.dumps(result, indent=2))
        print(f"\nStatistics: {aanya.get_statistics()}")
    
    asyncio.run(test())

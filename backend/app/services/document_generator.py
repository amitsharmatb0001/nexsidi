import json
import logging
from typing import Dict, Any
from app.services.ai_router import ai_router, TaskComplexity
from app.services.context_engine import context_engine
from app.services.prompt_engine import prompt_engine
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os

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
        """
        try:
            self.logger.info(f"📄 Generating SDD for project: {project_id}")
            
            context = context_engine.get_full_context(project_id)
            if not context:
                return "# SDD: No context available"

            # Enhanced prompt with Vikram and Vanya context
            prompt = f"""
            Generate a detailed System Design Document for project {project_id}.
            Include:
            1. Architecture Overview (from Vikram)
            2. Design System and UI/UX approach (from Vanya)
            3. Backend API details (from Shubham)
            4. Frontend Implementation (from Aanya)
            
            Full Context:
            {json.dumps(context, indent=2)}
            """
            
            response = await ai_router.generate(
                messages=[{"role": "user", "content": prompt}],
                task_type="architecture",
                complexity=TaskComplexity.COMPLEX
            )
            
            return response.content
            
        except Exception as e:
            self.logger.error(f"[ERROR] SDD generation failed: {e}")
            return f"# Error generating SDD\n\n{str(e)}"

    async def generate_design_pdf(self, project_id: str, design_data: Dict) -> str:
        """Task 2.1: Generate PDF documentation for Vanya's design."""
        try:
            filename = f"e:/nexsidi/workspace/{project_id}/docs/Design_System.pdf"
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            
            c = canvas.Canvas(filename, pagesize=letter)
            width, height = letter
            
            c.setFont("Helvetica-Bold", 24)
            c.drawString(100, height - 100, "NexSidi Design System")
            
            c.setFont("Helvetica", 14)
            c.drawString(100, height - 150, f"Project: {project_id}")
            
            # Draw Colors
            c.setFont("Helvetica-Bold", 18)
            c.drawString(100, height - 200, "Color Palette")
            
            colors = design_data.get("design_system", {}).get("colors", {})
            y = height - 230
            for name, value in colors.items():
                c.setFont("Helvetica", 12)
                c.drawString(120, y, f"{name}: {value}")
                y -= 20
                
            c.save()
            return filename
        except Exception as e:
            self.logger.error(f"[ERROR] Design PDF generation failed: {e}")
            raise

    def export_to_pdf(self, markdown_content: str, output_path: str):
        """Simple Markdown to PDF exporter (Task 2.2 fallback)"""
        c = canvas.Canvas(output_path, pagesize=letter)
        width, height = letter
        y = height - 50
        
        for line in markdown_content.split('\n'):
            if y < 50:
                c.showPage()
                y = height - 50
            c.setFont("Helvetica", 10)
            c.drawString(50, y, line[:100])
            y -= 15
        
        c.save()

# Global instance
document_generator = DocumentGenerator()

"""
Approval Service - Visual Preview & User Approval System
"""
from typing import Dict, Optional, List, Any
from enum import Enum
from datetime import datetime
import asyncio
import logging
from app.services.context_engine import context_engine

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("approval_service")

class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class ApprovalService:
    """
    Manages user approval checkpoints with visual previews
    """
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.logger = logging.getLogger(f"approval_service.{project_id}")
    
    async def create_design_preview_checkpoint(
        self, 
        blueprint: Dict,
        vanya_design: Dict
    ) -> Dict:
        """
        CHECKPOINT 1: After Vikram designs architecture and Vanya creates design system
        """
        self.logger.info("Creating design preview checkpoint...")
        
        # Generate interactive mockup URL (Mocked for now)
        mockup_url = f"https://preview.nexsidi.com/{self.project_id}/design"
        
        # Create checkpoint
        checkpoint = {
            "checkpoint_id": f"cp-{self.project_id}-design",
            "type": "design_preview",
            "status": ApprovalStatus.PENDING.value,
            "created_at": datetime.now().isoformat(),
            "preview_data": {
                "mockup_url": mockup_url,
                "mockup_html": vanya_design.get("preview_html", "<div>Mock Preview</div>"),
                "features_included": self._extract_features(blueprint),
                "cost_inr": blueprint.get("cost", 45000),
                "timeline_hours": blueprint.get("timeline_hours", 12),
                "design_preview": {
                    "primary_color": vanya_design.get("colors", {}).get("primary", "#667eea"),
                    "font": vanya_design.get("typography", {}).get("font_family", "Inter"),
                    "layout": vanya_design.get("layout_preview", "Standard Sidebar")
                }
            },
            "expires_at": None
        }
        
        # Store in context engine
        context_engine.store_context(self.project_id, f"checkpoint_design", checkpoint)
        
        # Notify user via WebSocket (Assuming manager is available)
        try:
            from app.api.websocket import manager
            await manager.broadcast_to_project(
                self.project_id,
                {
                    "type": "approval_required",
                    "checkpoint": checkpoint
                }
            )
        except Exception as e:
            self.logger.warning(f"Could not notify via WebSocket: {e}")
        
        return checkpoint
    
    async def create_testing_checkpoint(
        self,
        deployment_urls: Dict,
        test_credentials: Dict
    ) -> Dict:
        """
        CHECKPOINT 2: After code is built and deployed to test environment
        """
        self.logger.info("Creating testing checkpoint...")
        
        checkpoint = {
            "checkpoint_id": f"cp-{self.project_id}-testing",
            "type": "testing",
            "status": ApprovalStatus.PENDING.value,
            "created_at": datetime.now().isoformat(),
            "test_data": {
                "app_url": deployment_urls.get("frontend", ""),
                "api_url": deployment_urls.get("backend", ""),
                "test_credentials": test_credentials,
                "test_instructions": [
                    "1. Click the link above to open your app",
                    "2. Log in using the test credentials",
                    "3. Try the core features of your app",
                    "4. Verify responsive design on mobile",
                    "5. Check if all labels and colors are correct"
                ],
                "features_to_test": [
                    {"feature": "Authentication", "tested": False},
                    {"feature": "Main Dashboard", "tested": False},
                    {"feature": "Database operations", "tested": False}
                ]
            }
        }
        
        context_engine.store_context(self.project_id, f"checkpoint_testing", checkpoint)
        
        try:
            from app.api.websocket import manager
            await manager.broadcast_to_project(
                self.project_id,
                {
                    "type": "approval_required",
                    "checkpoint": checkpoint
                }
            )
        except Exception as e:
            self.logger.warning(f"Could not notify via WebSocket: {e}")
        
        return checkpoint
    
    async def wait_for_approval(self, checkpoint_id: str) -> Dict:
        """
        Wait for user to approve, reject or request changes
        """
        checkpoint_key = "checkpoint_design" if "design" in checkpoint_id else "checkpoint_testing"
        
        self.logger.info(f"Waiting for approval on {checkpoint_id}...")
        
        # Poll context engine for status change
        while True:
            checkpoint = context_engine.get_context(self.project_id, checkpoint_key)
            
            if not checkpoint:
                self.logger.warning(f"Checkpoint {checkpoint_id} not found in context")
                await asyncio.sleep(5)
                continue

            if checkpoint.get("status") != ApprovalStatus.PENDING.value:
                self.logger.info(f"Checkpoint {checkpoint_id} updated: {checkpoint['status']}")
                return {
                    "status": checkpoint["status"],
                    "user_feedback": checkpoint.get("user_feedback"),
                    "timestamp": checkpoint.get("updated_at", datetime.now().isoformat())
                }
            
            await asyncio.sleep(2)
    
    async def update_checkpoint_status(self, checkpoint_id: str, status: str, feedback: Optional[str] = None):
        """Update checkpoint status in storage"""
        checkpoint_key = "checkpoint_design" if "design" in checkpoint_id else "checkpoint_testing"
        checkpoint = context_engine.get_context(self.project_id, checkpoint_key)
        
        if checkpoint:
            checkpoint["status"] = status
            checkpoint["user_feedback"] = feedback
            checkpoint["updated_at"] = datetime.now().isoformat()
            context_engine.store_context(self.project_id, checkpoint_key, checkpoint)
            self.logger.info(f"Updated {checkpoint_id} to {status}")

    def _extract_features(self, blueprint: Dict) -> List[str]:
        """Extract user-friendly feature list from blueprint"""
        # Logic to convert technical endpoints or services to friendly names
        features = []
        services = blueprint.get("services", [])
        for svc in services:
            svc_name = svc.get("name", "").replace("-", " ").title()
            features.append(svc_name)
        
        if not features:
            features = ["User Authentication", "Main App Logic", "Database Storage"]
            
        return features

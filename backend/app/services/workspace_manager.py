"""
Workspace Manager - Project File Isolation
==========================================
"""

import os
import shutil
import logging
from typing import Dict
from pathlib import Path

logger = logging.getLogger("workspace_manager")


class WorkspaceManager:
    """Manage isolated workspaces for projects"""
    
    def __init__(self, base_path="/tmp/nexsidi"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Workspace manager initialized: {self.base_path}")
    
    def create_workspace(self, project_id: str) -> Dict[str, str]:
        """
        Create isolated workspace for project.
        
        Returns:
            Dict with paths: workspace, code_dir, test_dir, 
                           screenshot_dir, log_dir
        """
        workspace_path = self.base_path / project_id
        
        # Create directory structure
        directories = {
            'workspace': workspace_path,
            'code_dir': workspace_path / 'code',
            'test_dir': workspace_path / 'tests',
            'screenshot_dir': workspace_path / 'screenshots',
            'log_dir': workspace_path / 'logs'
        }
        
        for path in directories.values():
            path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[OK] Workspace created for {project_id}")
        return {k: str(v) for k, v in directories.items()}
    
    def cleanup_workspace(self, project_id: str):
        """Remove workspace after completion"""
        workspace_path = self.base_path / project_id
        
        if workspace_path.exists():
            shutil.rmtree(workspace_path)
            logger.info(f"🧹 Workspace cleaned for {project_id}")
    
    def get_workspace(self, project_id: str) -> Dict[str, str]:
        """Get existing workspace paths"""
        workspace_path = self.base_path / project_id
        
        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace not found for {project_id}")
        
        return {
            'workspace': str(workspace_path),
            'code_dir': str(workspace_path / 'code'),
            'test_dir': str(workspace_path / 'tests'),
            'screenshot_dir': str(workspace_path / 'screenshots'),
            'log_dir': str(workspace_path / 'logs')
        }


# Global instance
workspace_manager = WorkspaceManager()

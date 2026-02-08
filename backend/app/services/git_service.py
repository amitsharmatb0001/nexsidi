"""
GIT AUTOMATION SERVICE
======================
Location: app/services/git_service.py

Purpose: Automatic version control for generated code.
Fills Patent Gap #5 - "Git Integration / Persistence"

Features:
- Auto-init Git repo for each project
- Commit after each agent completes
- Branch management for iterations
- Diff-aware regeneration
- Full version history

"""

import os
import subprocess
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path


class GitService:
    """
    Automated Git operations for project version control.
    
    Each project gets its own Git repository with:
    - Initial commit (project structure)
    - Agent commits (backend, frontend, etc.)
    - Iteration branches (for change requests)
    - Deployment tags (v1.0, v1.1, etc.)
    """
    
    def __init__(self):
        self.logger = logging.getLogger("git_service")
    
    def init_repository(self, project_path: str, project_id: str) -> Dict[str, Any]:
        """
        Initialize Git repository for project.
        
        Args:
            project_path: Absolute path to project directory
            project_id: Project UUID
        
        Returns:
            {"status": "success", "repo_path": "..."}
        """
        try:
            self.logger.info(f"🔧 Initializing Git repo for {project_id}")
            
            # Init repo
            subprocess.run(
                ["git", "init"],
                cwd=project_path,
                check=True,
                capture_output=True
            )
            
            # Configure
            subprocess.run(
                ["git", "config", "user.name", "NexSidi AI"],
                cwd=project_path,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "ai@nexsidi.com"],
                cwd=project_path,
                check=True
            )
            
            # Create .gitignore
            gitignore_content = """
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
.env

# Node
node_modules/
npm-debug.log
yarn-error.log
.next/
dist/
build/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Database
*.db
*.sqlite
*.sqlite3

# Logs
*.log
logs/
"""
            gitignore_path = os.path.join(project_path, ".gitignore")
            with open(gitignore_path, "w") as f:
                f.write(gitignore_content)
            
            # Initial commit
            subprocess.run(
                ["git", "add", ".gitignore"],
                cwd=project_path,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit - Project structure"],
                cwd=project_path,
                check=True
            )
            
            self.logger.info(f"✅ Git repo initialized: {project_path}")
            
            return {
                "status": "success",
                "repo_path": project_path,
                "initial_commit": True
            }
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"❌ Git init failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def commit_agent_work(
        self,
        project_path: str,
        agent_name: str,
        message: str = None
    ) -> Dict[str, Any]:
        """
        Commit agent's work to repository.
        
        Args:
            project_path: Project directory
            agent_name: Which agent (shubham, aanya, etc.)
            message: Custom commit message
        
        Returns:
            {"status": "success", "commit_hash": "abc123..."}
        """
        try:
            # Add all changes
            subprocess.run(
                ["git", "add", "."],
                cwd=project_path,
                check=True
            )
            
            # Commit
            commit_msg = message or f"✨ {agent_name.upper()}: Generated code"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=project_path,
                check=True,
                capture_output=True
            )
            
            # Get commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_path,
                check=True,
                capture_output=True,
                text=True
            )
            commit_hash = result.stdout.strip()
            
            self.logger.info(f"✅ Committed {agent_name} work: {commit_hash[:8]}")
            
            return {
                "status": "success",
                "commit_hash": commit_hash,
                "message": commit_msg
            }
            
        except subprocess.CalledProcessError as e:
            # Check if nothing to commit
            if "nothing to commit" in str(e.stderr):
                return {
                    "status": "no_changes",
                    "message": "No changes to commit"
                }
            
            self.logger.error(f"❌ Commit failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def create_iteration_branch(
        self,
        project_path: str,
        iteration_number: int
    ) -> Dict[str, Any]:
        """
        Create branch for iteration/change request.
        
        Branches: iteration-1, iteration-2, etc.
        """
        try:
            branch_name = f"iteration-{iteration_number}"
            
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=project_path,
                check=True
            )
            
            self.logger.info(f"✅ Created branch: {branch_name}")
            
            return {
                "status": "success",
                "branch": branch_name
            }
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"❌ Branch creation failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def merge_iteration(
        self,
        project_path: str,
        iteration_branch: str
    ) -> Dict[str, Any]:
        """
        Merge iteration branch back to main.
        """
        try:
            # Switch to main
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=project_path,
                check=True
            )
            
            # Merge
            subprocess.run(
                ["git", "merge", iteration_branch, "--no-ff", "-m", f"Merge {iteration_branch}"],
                cwd=project_path,
                check=True
            )
            
            self.logger.info(f"✅ Merged {iteration_branch} to main")
            
            return {
                "status": "success",
                "merged": iteration_branch
            }
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"❌ Merge failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def tag_deployment(
        self,
        project_path: str,
        version: str
    ) -> Dict[str, Any]:
        """
        Tag a deployment version.
        
        Example: v1.0, v1.1, v2.0
        """
        try:
            subprocess.run(
                ["git", "tag", "-a", version, "-m", f"Deployment {version}"],
                cwd=project_path,
                check=True
            )
            
            self.logger.info(f"✅ Tagged deployment: {version}")
            
            return {
                "status": "success",
                "tag": version
            }
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"❌ Tagging failed: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_diff(
        self,
        project_path: str,
        file_path: str = None
    ) -> Dict[str, Any]:
        """
        Get diff of uncommitted changes.
        
        Used for diff-aware regeneration.
        """
        try:
            cmd = ["git", "diff"]
            if file_path:
                cmd.append(file_path)
            
            result = subprocess.run(
                cmd,
                cwd=project_path,
                check=True,
                capture_output=True,
                text=True
            )
            
            return {
                "status": "success",
                "diff": result.stdout
            }
            
        except subprocess.CalledProcessError as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_commit_history(
        self,
        project_path: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get commit history.
        
        Returns list of commits with hash, message, author, date.
        """
        try:
            result = subprocess.run(
                ["git", "log", f"-{limit}", "--pretty=format:%H|%s|%an|%ad", "--date=iso"],
                cwd=project_path,
                check=True,
                capture_output=True,
                text=True
            )
            
            commits = []
            for line in result.stdout.split("\n"):
                if line:
                    parts = line.split("|")
                    commits.append({
                        "hash": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "date": parts[3]
                    })
            
            return commits
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"❌ Failed to get history: {e}")
            return []


# Global instance
git_service = GitService()

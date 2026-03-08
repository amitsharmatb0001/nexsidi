"""Project Workspace — Persistent disk-backed file system.

PHASE-5: Files are saved on disk in proper project structure, not just
in-memory dicts. Provides dual-write (disk + VFS for backward compatibility).

PHASE-8: Git integration — auto-commit at milestones, tag at checkpoints.

Structure:
  /projects/{run_id}/
    backend/          — Backend code
    frontend/         — Frontend code
    mobile/           — Mobile code
    database/         — DB schemas, migrations, seeds
    .nexsidi/         — Metadata, reports, checkpoints
    .gitignore        — Auto-generated
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Resolve default workspace base directory per-platform.
# Override via NEXSIDI_WORKSPACE_DIR env var.
_DEFAULT_BASE_DIR: str = os.environ.get(
    "NEXSIDI_WORKSPACE_DIR",
    str(Path.home() / ".nexsidi" / "projects") if sys.platform == "win32" else "/projects",
)

# Default .gitignore for generated projects
_DEFAULT_GITIGNORE = """# Dependencies
node_modules/
__pycache__/
*.pyc
.venv/
venv/
env/

# Environment
.env
.env.local
.env.production

# Build output
dist/
build/
.next/
.nuxt/
out/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# NexSidi metadata
.nexsidi/
"""


class ProjectWorkspace:
    """Persistent disk-backed workspace for a project run."""

    def __init__(self, run_id: str, base_dir: str = "") -> None:
        self.run_id = run_id
        self.root = Path(base_dir or _DEFAULT_BASE_DIR) / run_id
        self._initialized = False

    def initialize(self) -> None:
        """Create directory structure. Called once when workspace is first used."""
        if self._initialized:
            return

        self.root.mkdir(parents=True, exist_ok=True)

        # Create standard subdirectories
        for subdir in ("backend", "frontend", "mobile", "database", ".nexsidi"):
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

        self._initialized = True
        logger.info("workspace_initialized", run_id=self.run_id, root=str(self.root))

    def write_file(self, path: str, content: str, agent: str = "") -> None:
        """Write file to disk.

        Also writes to VFS if available (dual-write for backward compatibility).
        """
        self.initialize()

        full_path = self.root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        # Dual-write to VFS (non-blocking, non-fatal)
        try:
            from app.services.vfs import get_vfs
            vfs = get_vfs()
            if vfs:
                asyncio.get_event_loop().create_task(
                    vfs.store_file(self.run_id, path, content, agent=agent)
                )
        except Exception:
            pass  # VFS write is optional

        logger.debug(
            "workspace_file_written",
            run_id=self.run_id,
            path=path,
            size=len(content),
            agent=agent,
        )

    def read_file(self, path: str) -> str | None:
        """Read file from disk (primary), VFS fallback."""
        full_path = self.root / path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")

        # Fallback to VFS
        try:
            from app.services.vfs import get_vfs
            vfs = get_vfs()
            if vfs:
                import asyncio
                return asyncio.get_event_loop().run_until_complete(
                    vfs.read_file(self.run_id, path)
                )
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller

        return None

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """List all files matching glob pattern, relative to workspace root."""
        self.initialize()
        return [
            str(p.relative_to(self.root))
            for p in self.root.glob(pattern)
            if p.is_file() and not str(p.relative_to(self.root)).startswith(".nexsidi")
        ]

    def get_docker_bind_mount(self) -> str:
        """Return path for Docker -v bind mount."""
        self.initialize()
        return str(self.root)

    def get_root(self) -> Path:
        """Return workspace root path."""
        return self.root

    # ── PHASE-8: Git Integration ─────────────────────────────────────

    def git_init(self) -> bool:
        """Initialize git repo in workspace root."""
        self.initialize()
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            # Write .gitignore
            gitignore_path = self.root / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text(_DEFAULT_GITIGNORE, encoding="utf-8")

            # Initial commit
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", "[nexsidi] Initialize project workspace"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )

            logger.info("workspace_git_init", run_id=self.run_id)
            return True
        except Exception as exc:
            logger.warning("workspace_git_init_failed", error=str(exc)[:200])
            return False

    def git_commit(self, message: str, agent: str = "nexsidi") -> bool:
        """Stage all changes and commit."""
        try:
            # Check if there are changes to commit
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not status.stdout.strip():
                return False  # Nothing to commit

            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", f"[{agent}] {message}"],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            logger.info("workspace_git_commit", run_id=self.run_id, agent=agent, message=message[:100])
            return True
        except Exception as exc:
            logger.warning("workspace_git_commit_failed", error=str(exc)[:200])
            return False

    def git_tag(self, tag: str, message: str) -> bool:
        """Create annotated tag for milestones."""
        try:
            subprocess.run(
                ["git", "tag", "-a", tag, "-m", message],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            logger.info("workspace_git_tag", run_id=self.run_id, tag=tag)
            return True
        except Exception as exc:
            logger.warning("workspace_git_tag_failed", error=str(exc)[:200])
            return False

    def git_push(self, remote_url: str, branch: str = "main") -> bool:
        """Push to remote repository."""
        try:
            # Add remote if not exists
            subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
            # Push
            result = subprocess.run(
                ["git", "push", "-u", "origin", branch, "--tags"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.warning("workspace_git_push_failed", stderr=result.stderr[:200])
                return False
            logger.info("workspace_git_push", run_id=self.run_id, remote=remote_url[:100])
            return True
        except Exception as exc:
            logger.warning("workspace_git_push_failed", error=str(exc)[:200])
            return False


# ── Singleton per run_id ─────────────────────────────────────────────

_workspaces: dict[str, ProjectWorkspace] = {}


def get_workspace(run_id: str, base_dir: str = "") -> ProjectWorkspace:
    """Get or create workspace for a pipeline run."""
    if run_id not in _workspaces:
        _workspaces[run_id] = ProjectWorkspace(run_id, base_dir)
    return _workspaces[run_id]


def cleanup_workspace(run_id: str) -> None:
    """Remove workspace from cache (does NOT delete files)."""
    _workspaces.pop(run_id, None)

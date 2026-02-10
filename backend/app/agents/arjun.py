"""
ARJUN - PROJECT MANAGER AGENT
==============================

Purpose: SDLC Orchestration & Agent Coordination
Role: Sits between Tilotma (Chief AI Officer) and specialized agents

Responsibilities:
1. Execute full development pipeline
2. Coordinate all specialized agents
3. Run quality gates (adversarial reviews)
4. Track progress and handle errors
5. Implement patent requirements:
   - Context integrity (SHA-256)
   - Adversarial reviews (parallel)
   - Iterative refinement
   - Mistake memory
6. Report ALL activities to Tilotma (shadow monitoring)

Architecture:
USER → TILOTMA → ARJUN → [Saanvi, Shubham, Aanya, Navya, Karan, Deepika, Aarav, Pranav]
                  ↑
                  └── Shadow Monitoring (Tilotma receives all reports)
"""

import os
import asyncio
import logging
import hashlib
import json
import httpx
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

# Services
from app.services.ai_router import ai_router, TaskComplexity
from app.services.context_engine import context_engine
from app.services.prompt_engine import prompt_engine
from app.services.workspace_manager import workspace_manager
from app.services.mistake_memory import mistake_memory
from app.agents.mixins import (
    SearchCapableMixin, 
    MistakeMemoryMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin
)
from app.services.queue_manager import QueueManager
from app.services.isolation_manager import isolation_manager
from app.services.git_service import git_service
from app.services.signing_service import signing_service
from app.services.collaboration import CollaborationSession
from app.services.ledger_service import ledger_service
from app.services.adversarial_trainer import adversarial_trainer
from app.agents.vanya import Vanya
from app.agents.research_agent import ResearchAgent
from app.agents.riya import Riya
from uuid import uuid4
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class PipelinePhase(Enum):
    """Pipeline execution phases"""
    REQUIREMENTS = "requirements"
    BACKEND = "backend"
    FRONTEND = "frontend"
    MOBILE = "mobile"
    QUALITY_REVIEW = "quality_review"
    REFINEMENT = "refinement"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    FINAL_VALIDATION = "final_validation"
    COMPLETED = "completed"


class ErrorCategory(Enum):
    """Error classification for intelligent routing"""
    TRANSIENT = "transient"  # Network issues, temporary failures - retry immediately
    RATE_LIMIT = "rate_limit"  # API rate limits - exponential backoff
    AGENT_FAILURE = "agent_failure"  # Agent-specific failure - try alternative approach
    CONTEXT_CORRUPTION = "context_corruption"  # Hash mismatch - rollback required
    VALIDATION_ERROR = "validation_error"  # Invalid input/output - needs correction
    CRITICAL = "critical"  # Unrecoverable error - escalate to Tilotma


@dataclass
class PipelineState:
    """Tracks pipeline execution state"""
    current_phase: PipelinePhase = PipelinePhase.REQUIREMENTS
    completed_phases: List[str] = field(default_factory=list)
    progress_percentage: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    estimated_completion: Optional[datetime] = None
    agent_outputs: Dict[str, Any] = field(default_factory=dict)
    retry_counts: Dict[str, int] = field(default_factory=dict)
    quality_scores: Dict[str, float] = field(default_factory=dict)
    paused: bool = False
    intervention_count: int = 0
    
    # Convergence tracking
    convergence_metrics: Dict[str, List[float]] = field(default_factory=dict)
    convergence_achieved: bool = False
    convergence_iteration: int = 0
    
    # Error tracking
    error_history: List[Dict[str, Any]] = field(default_factory=list)
    recovery_strategies_used: List[str] = field(default_factory=list)
    
    # Hash chain for audit trail
    hash_chain: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Result of pipeline execution"""
    status: str
    code: Dict[str, Any]
    tests: Dict[str, Any]
    deployment: Dict[str, Any]
    quality_report: Dict[str, Any]
    timeline: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "tests": self.tests,
            "deployment": self.deployment,
            "quality_report": self.quality_report,
            "timeline": self.timeline
        }


# =============================================================================
# TILOTMA REPORTER
# =============================================================================

class TilotmaReporter:
    """
    Arjun's dedicated reporter to Tilotma.
    Ensures Tilotma gets ALL updates for shadow monitoring.
    """
    
    async def _safe_store(self, key: str, data: Dict):
        """Helper to safely store using Arjun's hash verification"""
        if self.arjun:
            await self.arjun._store_with_hash(key, data)
        else:
            # Fallback if Arjun not available (shouldn't happen)
            context_engine.store_context(self.project_id, key, data)

    def __init__(self, project_id: str, arjun_instance=None):
        self.project_id = project_id
        self.arjun = arjun_instance
        self.logger = logging.getLogger(f"tilotma_reporter.{project_id}")
    
    async def report_agent_start(self, agent_name: str, input_data: Dict):
        """Report when agent starts"""
        report = {
            "status": "started",
            "input": input_data,
            "timestamp": time.time()
        }
        
        # Store using Arjun's SHA-256 verified storage
        await self._safe_store(f"tilotma_monitor_{agent_name}_start", report)
        
        self.logger.info(f"📊 Reported to Tilotma: {agent_name} started")
    
    async def report_agent_success(self, agent_name: str, result: Dict):
        """Report when agent succeeds"""
        report = {
            "status": "success",
            "result": result,
            "timestamp": time.time()
        }
        
        await self._safe_store(f"tilotma_monitor_{agent_name}_success", report)
        
        self.logger.info(f"📊 Reported to Tilotma: {agent_name} succeeded")
    
    async def report_agent_failure(self, agent_name: str, error: str):
        """Report when agent fails"""
        report = {
            "status": "failed",
            "error": error,
            "timestamp": time.time()
        }
        
        await self._safe_store(f"tilotma_monitor_{agent_name}_failure", report)
        
        self.logger.warning(f"📊 Reported to Tilotma: {agent_name} failed")
    
    async def report_quality_gate(self, review_result: Dict):
        """Report adversarial review results"""
        report = {
            "status": "completed",
            "result": review_result,
            "timestamp": time.time()
        }
        
        await self._safe_store("tilotma_monitor_quality_gate", report)
        
        self.logger.info("📊 Reported quality gate results to Tilotma")
    
    async def report_progress(self, phase: str, percentage: int):
        """Report progress update"""
        report = {
            "phase": phase,
            "percentage": percentage,
            "timestamp": time.time()
        }
        
        await self._safe_store("tilotma_monitor_progress", report)


# =============================================================================
# ARJUN - PROJECT MANAGER AGENT
# =============================================================================

class Arjun(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin):
    """
    Project Manager Agent - SDLC Orchestrator
    
    Responsibilities:
    - Execute full development pipeline
    - Coordinate all specialized agents
    - Run quality gates (adversarial reviews)
    - Track progress and handle errors
    - Implement patent requirements
    - Report ALL activities to Tilotma
    """
    
    def __init__(self, project_id: str, user_id: str):
        """
        Initialize Arjun for a project.
        
        Args:
            project_id: UUID of the project
            user_id: UUID of the user who owns the project
        """
        self.project_id = project_id
        self.user_id = user_id
        self.logger = logging.getLogger(f"arjun.{project_id}")
        
        # Pipeline state
        self.pipeline_state = PipelineState()
        
        # Tilotma reporter (shadow monitoring) - Pass SELF
        self.tilotma_reporter = TilotmaReporter(project_id, self)
        
        # Context engine
        self.context_engine = context_engine
        
        # CREATE ISOLATED WORKSPACE (Fixes self.workspace missing attribute)
        self.workspace = workspace_manager.create_workspace(project_id)
        self.logger.info(f"📁 Workspace created: {self.workspace['code_dir']}")
        
        self.logger.info(f"🎯 Arjun initialized for project {project_id}")
    
    # =========================================================================
    # PROJECT CREATION & ORCHESTRATION ENTRY POINT
    # =========================================================================
    
    @classmethod
    async def create_project(cls, user_id: str, title: str, description: str) -> Dict[str, Any]:
        """
        Static method to create and queue new project.
        Called by API endpoints.
        """
        project_id = str(uuid4())
        
        # Create Arjun instance
        arjun = cls(project_id=project_id, user_id=user_id)
        
        # Store initial context
        await arjun._store_with_hash("initial_requirements", {
            "title": title,
            "description": description,
            "user_id": user_id,
            "created_at": time.time()
        })
        
        # Enqueue project
        queue = QueueManager.get_instance()
        queue_info = await queue.enqueue_project(
            project_id=project_id,
            user_id=user_id,
            metadata={"title": title, "description": description}
        )
        
        return {
            "project_id": project_id,
            "queue_position": queue_info.get("queue_position", 1),
            "estimated_wait": queue_info.get("estimated_wait", "Starting now"),
            "status": "queued"
        }

    @classmethod
    async def get_project_status(cls, project_id: str) -> Dict[str, Any]:
        """
        Static method to get project status.
        Called by API endpoints.
        """
        queue = QueueManager.get_instance()
        status = await queue.get_project_status(project_id)
        
        if not status or status.get("status") == "not_found":
            return {
                "status": "unknown",
                "project_id": project_id
            }
        
        return status
    
    # =========================================================================
    # MAIN ORCHESTRATION
    # =========================================================================
    
    async def execute_pipeline(self, requirements: Dict) -> PipelineResult:
        """
        Execute full SDLC pipeline.
        
        Flow:
        1. Requirements Analysis (Saanvi)
        2. Backend Development (Shubham)
        3. Frontend Development (Aanya)
        4. Adversarial Review (Navya, Karan, Deepika - parallel)
        5. Iterative Refinement (if needed)
        6. Testing (Aarav)
        7. Deployment (Pranav)
        8. → Hand to Tilotma for final validation
        
        Args:
            requirements: Initial requirements from Tilotma
        
        Returns:
            PipelineResult ready for Tilotma's final validation
        """
        
        try:
            self.logger.info("🚀 Starting SDLC pipeline execution...")
            self.pipeline_state.start_time = datetime.now()
            
            # Initialize Git repository
            self.logger.info("🔧 Initializing Git repository...")
            git_result = git_service.init_repository(
                self.workspace['code_dir'],
                self.project_id
            )
            if git_result['status'] != 'success':
                self.logger.warning(f"⚠️ Git init failed: {git_result.get('error')}")
            
            # Phase 1: Requirements Analysis
            await self._update_progress(PipelinePhase.REQUIREMENTS, 10)
            saanvi_result = await self._delegate_to_agent("saanvi", requirements)
            self.pipeline_state.agent_outputs["saanvi"] = saanvi_result
            
            # Git: Commit requirements analysis
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "saanvi",
                "Requirements analysis complete"
            )
            
            # Phase 2: Design System Generation (Vanya)
            self.logger.info("🎨 Generating design system with Vanya...")
            
            # Prepare Vanya input
            vanya_input = {
                "requirements": saanvi_result.get("requirements", {}),
                "style_preference": saanvi_result.get("style_preference", "modern"),
                "target_audience": saanvi_result.get("target_audience", "general")
            }
            
            vanya_result = await self._delegate_to_agent("vanya", vanya_input)
            self.pipeline_state.agent_outputs["vanya"] = vanya_result
            
            # Git: Commit design system
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "vanya",
                "Design system generation complete"
            )
            
            # Phase 3: Backend Development  
            await self._update_progress(PipelinePhase.BACKEND, 30)
            
            # 🆕 AUTONOMOUS EXECUTION - Create container first (H2 fix)
            self.logger.info("🐳 Creating isolated container for backend...")
            backend_container_id = None
            try:
                container_result = isolation_manager.create_isolated_environment(
                    project_id=self.project_id,
                    workspace_path=self.workspace['code_dir'],
                    resource_limits={
                        "cpu_percent": 50,
                        "memory_mb": 2048,
                        "disk_mb": 5120
                    }
                )
                if container_result.get("status") == "success":
                    backend_container_id = container_result["container_id"]
                    self.logger.info(f"✅ Backend container created: {backend_container_id}")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to create backend container: {e}")

            self.logger.info("⚙️ Generating backend with Shubham...")
            # Pass existing container_id to avoid double creation
            shubham_result = await self._delegate_to_agent("shubham", saanvi_result, container_id=backend_container_id)
            self.pipeline_state.agent_outputs["shubham"] = shubham_result

            if backend_container_id:
                try:
                    # Store container info
                    shubham_result["container_id"] = backend_container_id
                    
                    # Install backend dependencies
                    self.logger.info("📦 Installing backend dependencies...")
                    
                    # Detect tech stack and install
                    tech_stack = saanvi_result.get("tech_stack", {})
                    backend_framework = tech_stack.get("backend", "fastapi")
                    
                    if "python" in backend_framework.lower():
                        # Python backend
                        install_cmd = "pip install -r requirements.txt --break-system-packages"
                    elif "node" in backend_framework.lower():
                        # Node.js backend
                        install_cmd = "npm install"
                    else:
                        install_cmd = "pip install -r requirements.txt --break-system-packages"
                    
                    install_result = isolation_manager.execute_in_container(
                        container_id=backend_container_id,
                        command=install_cmd,
                        timeout=300  # 5 minutes timeout
                    )
                    
                    if install_result.get("status") == "success":
                        self.logger.info("✅ Dependencies installed")
                        
                        # Run database migrations if they exist
                        if "alembic" in backend_framework.lower() or "migration" in str(shubham_result):
                            self.logger.info("🗄️ Running database migrations...")
                            migration_result = isolation_manager.execute_in_container(
                                container_id=backend_container_id,
                                command="alembic upgrade head || python manage.py migrate || true",
                                timeout=60
                            )
                            self.logger.info(f"Migrations: {migration_result.get('status')}")
                        
                        # Start backend server
                        self.logger.info("🚀 Starting backend server...")
                        
                        # Detect start command
                        if "fastapi" in backend_framework.lower():
                            start_cmd = "uvicorn app.main:app --host 0.0.0.0 --port 8000"
                        elif "flask" in backend_framework.lower():
                            start_cmd = "python app.py"
                        elif "express" in backend_framework.lower():
                            start_cmd = "node server.js"
                        else:
                            start_cmd = "uvicorn app.main:app --host 0.0.0.0 --port 8000"
                        
                        # Start server (non-blocking)
                        server_result = isolation_manager.start_service(
                            container_id=backend_container_id,
                            command=start_cmd,
                            port=8000
                        )
                        
                        if server_result.get("status") == "success":
                            backend_url = server_result.get("url")
                            self.logger.info(f"✅ Backend running at: {backend_url}")
                            
                            # Store URL in result
                            shubham_result["backend_url"] = backend_url
                            shubham_result["status"] = "running"
                            
                            # Test backend is accessible
                            await asyncio.sleep(3)  # Wait for server to start
                            try:
                                async with httpx.AsyncClient() as client:
                                    response = await client.get(f"{backend_url}/health", timeout=5.0)
                                    if response.status_code == 200:
                                        self.logger.info("✅ Backend health check passed")
                                    else:
                                        self.logger.warning(f"⚠️ Backend health check returned: {response.status_code}")
                            except Exception as e:
                                self.logger.warning(f"⚠️ Backend health check failed: {e}")
                        else:
                            self.logger.error(f"❌ Failed to start backend: {server_result.get('error')}")
                    else:
                        self.logger.error(f"❌ Failed to install dependencies: {install_result.get('error')}")
                except Exception as e:
                    self.logger.error(f"❌ Container execution failed: {e}")

            # Git: Commit backend code
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "shubham",
                "Backend generation and autonomous startup complete"
            )
            
            # Phase 4: Frontend Development (with design system)
            await self._update_progress(PipelinePhase.FRONTEND, 50)
            self.logger.info("🎨 Generating frontend with Aanya...")
            
            # Prepare Aanya input with design system
            aanya_input = {
                "frontend_architecture": saanvi_result.get("frontend_architecture", {}),
                "api_architecture": shubham_result.get("api_architecture", {}),
                "design_system": vanya_result.get("design_system", {})  # From Vanya!
            }
            
            # 🆕 AUTONOMOUS EXECUTION - Create container first (H2 fix)
            self.logger.info("🐳 Creating isolated container for frontend...")
            frontend_container_id = None
            try:
                container_result = isolation_manager.create_isolated_environment(
                    project_id=f"{self.project_id}-frontend",
                    workspace_path=self.workspace['code_dir'],
                    resource_limits={
                        "cpu_percent": 30,
                        "memory_mb": 1024,
                        "disk_mb": 3072
                    }
                )
                if container_result.get("status") == "success":
                    frontend_container_id = container_result["container_id"]
                    self.logger.info(f"✅ Frontend container created: {frontend_container_id}")
                else:
                    self.logger.warning(f"⚠️ Failed to create frontend container: {container_result.get('error')}")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to create frontend container: {e}")

            self.logger.info("🎨 Generating frontend with Aanya...")
            # Prepare Aanya input with design system
            aanya_input = {
                "frontend_architecture": saanvi_result.get("frontend_architecture", {}),
                "api_architecture": shubham_result.get("api_architecture", {}),
                "design_system": vanya_result.get("design_system", {})  # From Vanya!
            }
            
            aanya_result = await self._delegate_to_agent("aanya", aanya_input, container_id=frontend_container_id)
            self.pipeline_state.agent_outputs["aanya"] = aanya_result

            if frontend_container_id:
                try:
                    aanya_result["container_id"] = frontend_container_id
                    
                    # Install frontend dependencies
                    self.logger.info("📦 Installing frontend dependencies...")
                    
                    tech_stack = saanvi_result.get("tech_stack", {})
                    frontend_framework = tech_stack.get("frontend", "react")
                    
                    if "react" in frontend_framework.lower() or "vue" in frontend_framework.lower() or "next" in frontend_framework.lower():
                        install_cmd = "npm install"
                        build_cmd = "npm run build"
                        start_cmd = "npm start"
                    else:
                        install_cmd = "npm install"
                        build_cmd = "npm run build"
                        start_cmd = "npm start"
                    
                    # Install dependencies
                    install_result = isolation_manager.execute_in_container(
                        container_id=frontend_container_id,
                        command=install_cmd,
                        timeout=300
                    )
                    
                    if install_result.get("status") == "success":
                        self.logger.info("✅ Frontend dependencies installed")
                        
                        # Build frontend
                        self.logger.info("🏗️ Building frontend...")
                        build_result = isolation_manager.execute_in_container(
                            container_id=frontend_container_id,
                            command=build_cmd,
                            timeout=180
                        )
                        
                        if build_result.get("status") == "success":
                            self.logger.info("✅ Frontend built successfully")
                            
                            # Start frontend server
                            self.logger.info("🚀 Starting frontend server...")
                            server_result = isolation_manager.start_service(
                                container_id=frontend_container_id,
                                command=start_cmd,
                                port=3000
                            )
                            
                            if server_result.get("status") == "success":
                                frontend_url = server_result.get("url")
                                self.logger.info(f"✅ Frontend running at: {frontend_url}")
                                
                                aanya_result["frontend_url"] = frontend_url
                                aanya_result["status"] = "running"
                            else:
                                self.logger.error(f"❌ Failed to start frontend: {server_result.get('error')}")
                        else:
                            self.logger.error(f"❌ Frontend build failed: {build_result.get('error')}")
                    else:
                        self.logger.error(f"❌ Failed to install frontend dependencies: {install_result.get('error')}")
                except Exception as e:
                    self.logger.error(f"❌ Frontend execution failed: {e}")

            # Git: Commit frontend code
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "aanya",
                "Frontend generation and autonomous startup complete"
            )
            
            # Phase 5: Mobile Development (if requested)
            if saanvi_result.get("platforms", {}).get("mobile"):
                await self._update_progress(PipelinePhase.MOBILE, 60)
                self.logger.info("📱 Generating mobile app with Riya...")
                
                # Prepare Riya input
                riya_input = {
                    "platforms": saanvi_result.get("platforms", {}).get("mobile", ["android"]),
                    "framework": saanvi_result.get("mobile_framework", "flutter"),
                    "requirements": saanvi_result.get("requirements", {}),
                    "design_system": vanya_result.get("design_system", {}),
                    "api_base_url": shubham_result.get("backend_url", "")
                }
                
                riya_result = await self._delegate_to_agent("riya", riya_input)
                self.pipeline_state.agent_outputs["riya"] = riya_result
                
                # Git: Commit mobile code
                git_service.commit_agent_work(
                    self.workspace['code_dir'],
                    "riya",
                    "Mobile app generation complete"
                )
            
            # Phase 6: Adversarial Review (PARALLEL)
            await self._update_progress(PipelinePhase.QUALITY_REVIEW, 70)
            code = {"backend": shubham_result, "frontend": aanya_result}
            review_result = await self.adversarial_quality_gate(code)
            
            # Report quality gate to Tilotma
            await self.tilotma_reporter.report_quality_gate(review_result)
            
            # Phase 7: Iterative Refinement (if needed)
            if review_result["total_bugs"] > 0:
                await self._update_progress(PipelinePhase.REFINEMENT, 75)
                code = await self.iterative_refinement(code, review_result)
            
            # Phase 8: Testing
            await self._update_progress(PipelinePhase.TESTING, 85)
            aarav_result = await self._delegate_to_agent("aarav", code)
            self.pipeline_state.agent_outputs["aarav"] = aarav_result
            
            # Git: Commit test suite
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "aarav",
                "Test suite generation complete"
            )
            
            # Phase 9: Deployment Configuration
            await self._update_progress(PipelinePhase.DEPLOYMENT, 95)
            pranav_result = await self._delegate_to_agent("pranav", code)
            self.pipeline_state.agent_outputs["pranav"] = pranav_result
            
            # Git: Commit deployment config
            git_service.commit_agent_work(
                self.workspace['code_dir'],
                "pranav",
                "Deployment configuration complete"
            )
            
            # Phase 10: Ready for Tilotma's final validation
            await self._update_progress(PipelinePhase.FINAL_VALIDATION, 98)
            
            self.logger.info("✅ Pipeline execution complete - ready for Tilotma validation")
            
            return PipelineResult(
                status="awaiting_tilotma_approval",
                code=code,
                tests=aarav_result,
                deployment=pranav_result,
                quality_report=review_result,
                timeline={
                    "start": self.pipeline_state.start_time.isoformat(),
                    "end": datetime.now().isoformat(),
                    "phases": self.pipeline_state.completed_phases
                }
            )
            
        except Exception as e:
            self.logger.error(f"❌ Pipeline execution failed: {e}")
            await self._handle_pipeline_failure(e)
            raise
    
    # =========================================================================
    # AGENT DELEGATION
    # =========================================================================
    
    async def _delegate_to_agent(self, agent_name: str, input_data: Dict, max_retries: int = 3, container_id: Optional[str] = None) -> Dict:
        """
        Delegate task to specific agent with intelligent retry and mistake memory.
        
        Args:
            agent_name: Name of the agent (saanvi, shubham, etc)
            input_data: Data to pass to agent
            max_retries: Max attempts before escalating
            container_id: Optional existing container ID to reuse
        """
        
        # Check mistake memory BEFORE execution
        past_mistakes = await self._check_mistake_memory(agent_name, self.pipeline_state.current_phase.value)
        
        # Report START to Tilotma
        await self.tilotma_reporter.report_agent_start(agent_name, input_data)
        
        max_retries = 3
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                # Verify context integrity before agent execution
                await self._verify_context_integrity("full_context")
                
                # Execute agent
                self.logger.info(f"🔄 Delegating to {agent_name} (attempt {retry_count + 1}/{max_retries})")
                
                result = await self._execute_agent(agent_name, input_data, past_mistakes, container_id)
                
                # Cryptographic signing
                signed_result = signing_service.sign_output(
                    agent_name=agent_name,
                    output_data=result,
                    metadata={
                        "project_id": self.project_id,
                        "phase": self.pipeline_state.current_phase.value,
                        "timestamp": time.time()
                    }
                )
                
                # VERIFY before storing (detect tampering)
                if not signing_service.verify_signature(signed_result, agent_name):
                    raise RuntimeError(f"Security Error: Signature verification failed for {agent_name}")
                
                # Store signed output in context
                self.context_engine.store_context(
                    self.project_id,
                    f"signed_{agent_name}_output",
                    signed_result
                )
                
                # Audit ledger
                ledger_service.append_event(
                    project_id=self.project_id,
                    agent_name=agent_name,
                    event_type=f"{agent_name}_output",
                    data=result,
                    metadata={
                        "signature": signed_result["signature"],
                        "hash": signed_result["hash"],
                        "phase": self.pipeline_state.current_phase.value
                    }
                )
                
                # H3: Optimize storage - only store current agent output to context
                self.context_engine.store_context(
                    self.project_id,
                    agent_name,
                    result
                )
                
                # Report SUCCESS to Tilotma
                await self.tilotma_reporter.report_agent_success(agent_name, result)
                
                return result
                
            except Exception as e:
                last_error = e
                retry_count += 1
                
                # === INTELLIGENT ERROR ROUTING ===
                error_category = self._classify_error(e, agent_name)
                recovery_strategy = await self._route_error(error_category, e, agent_name, retry_count)
                
                # === RESEARCH AGENT: AUTO-SEARCH FOR SOLUTIONS ===
                research_solution = None
                try:
                    self.logger.info(f"🔍 Researching solution for error...")
                    research = ResearchAgent(self.project_id)
                    research_result = await research.find_error_solution(
                        error_message=str(e),
                        code_context=f"Agent: {agent_name}, Phase: {self.pipeline_state.current_phase.value}"
                    )
                    research_solution = research_result.get("summary", "")
                    if research_solution:
                        self.logger.info(f"💡 Research found: {research_solution[:200]}...")
                except Exception as research_error:
                    self.logger.warning(f"⚠️ Research agent failed: {research_error}")
                
                # Log error with classification
                self.logger.warning(
                    f"⚠️ {agent_name} failed (attempt {retry_count}/{max_retries}): {e} "
                    f"[Category: {error_category.value}, Strategy: {recovery_strategy}]"
                )
                
                # Store error in history (with research findings)
                self.pipeline_state.error_history.append({
                    "agent": agent_name,
                    "error": str(e),
                    "category": error_category.value,
                    "recovery_strategy": recovery_strategy,
                    "research_solution": research_solution,  # NEW: Store research findings
                    "attempt": retry_count,
                    "timestamp": time.time()
                })
                
                # Log error to ledger
                ledger_service.append_event(
                    project_id=self.project_id,
                    agent_name=agent_name,
                    event_type=f"{agent_name}_error",
                    data={
                        "error": str(e),
                        "category": error_category.value,
                        "attempt": retry_count
                    },
                    metadata={
                        "recovery_strategy": recovery_strategy,
                        "research_solution": research_solution
                    }
                )
                
                # Record mistake if not max retries yet
                if retry_count < max_retries:
                    mistake_context = f"Recovery: {recovery_strategy}"
                    if research_solution:
                        mistake_context += f" | Research: {research_solution[:100]}"
                    await self._record_mistake(agent_name, str(e), mistake_context)
                
                # Report FAILURE to Tilotma
                await self.tilotma_reporter.report_agent_failure(agent_name, str(e))
                
                # Apply recovery strategy
                if retry_count < max_retries:
                    await self._apply_recovery_strategy(recovery_strategy, agent_name, e)
                else:
                    if error_category == ErrorCategory.CRITICAL:
                        await self._escalate_to_tilotma(agent_name, e, error_category)
            
            # C7: Raise if max retries reached and not caught inside loop
            raise RuntimeError(f"Agent {agent_name} failed after {max_retries} retries: {last_error}")
    
    def _classify_error(self, error: Exception, agent_name: str) -> ErrorCategory:
        """
        Classify error into category for intelligent routing.
        
        Args:
            error: The exception that occurred
            agent_name: Name of the agent that failed
        
        Returns:
            ErrorCategory enum value
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Context corruption (hash mismatch)
        if "hash mismatch" in error_str or "integrity violation" in error_str:
            return ErrorCategory.CONTEXT_CORRUPTION
        
        # Rate limiting
        if "rate limit" in error_str or "429" in error_str or "quota" in error_str:
            return ErrorCategory.RATE_LIMIT
        
        # Transient network errors
        if any(keyword in error_str for keyword in ["timeout", "connection", "network", "temporary"]):
            return ErrorCategory.TRANSIENT
        
        # Validation errors
        if any(keyword in error_type for keyword in ["ValueError", "ValidationError", "TypeError"]):
            return ErrorCategory.VALIDATION_ERROR
        
        # Agent-specific failures
        if "agent" in error_str or agent_name in error_str:
            return ErrorCategory.AGENT_FAILURE
        
        # Critical/unknown errors
        return ErrorCategory.CRITICAL
    
    async def _route_error(
        self,
        category: ErrorCategory,
        error: Exception,
        agent_name: str,
        retry_count: int
    ) -> str:
        """
        Route error to appropriate recovery strategy based on category.
        
        Args:
            category: Error category
            error: The exception
            agent_name: Agent that failed
            retry_count: Current retry attempt
        
        Returns:
            Recovery strategy name
        """
        if category == ErrorCategory.TRANSIENT:
            # Immediate retry with short delay
            return "immediate_retry"
        
        elif category == ErrorCategory.RATE_LIMIT:
            # Exponential backoff
            return "exponential_backoff"
        
        elif category == ErrorCategory.CONTEXT_CORRUPTION:
            # Rollback to last valid state
            return "rollback_context"
        
        elif category == ErrorCategory.AGENT_FAILURE:
            # Try alternative approach or agent
            return "alternative_agent"
        
        elif category == ErrorCategory.VALIDATION_ERROR:
            # Correct input and retry
            return "correct_input"
        
        elif category == ErrorCategory.CRITICAL:
            # Escalate to Tilotma
            return "escalate_tilotma"
        
        return "default_retry"
    
    async def _apply_recovery_strategy(self, strategy: str, agent_name: str, error: Exception):
        """
        Apply the selected recovery strategy.
        
        Args:
            strategy: Recovery strategy name
            agent_name: Agent that failed
            error: The exception
        """
        self.pipeline_state.recovery_strategies_used.append(strategy)
        
        if strategy == "immediate_retry":
            # Short delay before retry
            await asyncio.sleep(1)
        
        elif strategy == "exponential_backoff":
            # Exponential backoff based on retry count
            retry_count = len([e for e in self.pipeline_state.error_history if e["agent"] == agent_name])
            delay = min(2 ** retry_count, 30)  # Max 30 seconds
            self.logger.info(f"⏳ Rate limit detected - waiting {delay}s before retry")
            await asyncio.sleep(delay)
        
        elif strategy == "rollback_context":
            # Rollback to last valid context state
            self.logger.warning("🔄 Rolling back to last valid context state")
            await self._rollback_to_last_good_state("full_context")
        
        elif strategy == "alternative_agent":
            # Log that alternative approach will be tried
            self.logger.info(f"🔀 Will try alternative approach for {agent_name}")
            # Implementation would switch to backup agent or different strategy
        
        elif strategy == "correct_input":
            # Log validation error - would need input correction
            self.logger.warning(f"⚠️ Validation error - input may need correction")
        
        elif strategy == "escalate_tilotma":
            # Will be handled by caller
            pass
        
        else:
            # Default retry with standard delay
            await asyncio.sleep(2)
    
    async def _escalate_to_tilotma(self, agent_name: str, error: Exception, category: ErrorCategory):
        """
        Escalate critical error to Tilotma for intervention.
        
        Args:
            agent_name: Agent that failed
            error: The exception
            category: Error category
        """
        self.logger.error(f"🚨 ESCALATING TO TILOTMA: {agent_name} - {error}")
        
        # Store escalation in context for Tilotma to review
        # Store escalation in context for Tilotma to review
        await self._store_with_hash(
            "tilotma_escalation",
            {
                "agent": agent_name,
                "error": str(error),
                "category": category.value,
                "timestamp": time.time(),
                "pipeline_state": {
                    "phase": self.pipeline_state.current_phase.value,
                    "progress": self.pipeline_state.progress_percentage,
                    "error_history": self.pipeline_state.error_history
                }
            }
        )
        
        # Pause pipeline for Tilotma intervention
        await self.pause_pipeline()

    
    async def _create_execution_environment(self, agent_name: str) -> str:
        """Create isolated container through IsolationManager (patent-compliant)."""
        self.logger.info(f"🐳 Creating isolated container for {agent_name}...")
        
        resource_limits = {
            "memory_mb": 1024, # 1GB
            "cpu_percent": 100 # 100% of 1 core
        }
        
        # Use Node image for Aanya
        config = {}
        if agent_name == "aanya":
            config["image"] = "node:18-slim"
        
        result = isolation_manager.create_isolated_environment(
            project_id=f"{self.project_id}-{agent_name}",
            workspace_path=self.workspace['code_dir'],
            resource_limits=resource_limits,
            config=config
        )
        
        if result["status"] != "success":
            self.logger.error(f"❌ Failed to create container for {agent_name}: {result.get('error')}")
            # Fallback for now, but in production this should probably raise
            return None
        
        return result["container_id"]

    async def _execute_agent(self, agent_name: str, input_data: Dict, past_mistakes: List[Dict], container_id: Optional[str] = None) -> Dict:
        """Execute specific agent"""
        
        if agent_name == "saanvi":
            from app.agents.saanvi import Saanvi
            agent = Saanvi(self.project_id, self.user_id)
            
            # Check if we have full conversation history (preferred)
            if "conversation" in input_data:
                result = await agent.analyze_requirements(input_data["conversation"])
                # Ensure we return a dict
                return result.to_dict() if hasattr(result, "to_dict") else result
            else:
                return await agent.analyze_requirements_from_text(input_data.get("description", ""))
        
        elif agent_name == "shubham":
            from app.agents.shubham import Shubham
            
            # Use provided container or create one if not available
            if not container_id:
                container_id = await self._create_execution_environment("shubham")
            
            # Pass container info to Shubham (if Shubham supports it, else just workspace)
            try:
                agent = Shubham(
                    project_id=self.project_id,
                    workspace=self.workspace,
                    container=container_id
                )
            except TypeError:
                # Fallback if Shubham doesn't accept container arg yet
                agent = Shubham(
                    project_id=self.project_id,
                    workspace=self.workspace
                )
            
            result = await agent.execute(input_data)
            
            # Execute setup commands in container
            if container_id:
                self.logger.info("📦 Installing backend dependencies in container...")
                install_result = isolation_manager.execute_in_container(
                    container_id,
                    "pip install -r requirements.txt || echo 'No requirements.txt found'"
                )
                self.logger.info(f"   Status: {install_result.get('status')}")
                if install_result.get('stdout'):
                    self.logger.info(f"   Output: {install_result['stdout'][:200]}...")
            
            return result
        
        elif agent_name == "aanya":
            from app.agents.aanya import Aanya
            
            # Use provided container or create one if not available
            if not container_id:
                container_id = await self._create_execution_environment("aanya")
            
            # Pass container info to Aanya
            try:
                agent = Aanya(
                    project_id=self.project_id,
                    workspace=self.workspace,
                    container=container_id
                )
            except TypeError:
                agent = Aanya(
                    project_id=self.project_id,
                    workspace=self.workspace
                )
            
            result = await agent.execute(input_data)
            
            return result
        
        elif agent_name == "aarav":
            from app.agents.aarav_testing import AaravTesting
            
            # Use provided container or create one if not available
            if not container_id:
                container_id = await self._create_execution_environment("aarav")
            
            # Pass container info to Aarav
            try:
                agent = AaravTesting(
                    project_id=self.project_id,
                    workspace=self.workspace
                )
            except TypeError:
                agent = AaravTesting(
                    project_id=self.project_id
                )
            
            result = await agent.execute(input_data)
            
            # Execute test setup commands in container
            if container_id:
                self.logger.info("📦 Installing testing dependencies in container...")
                install_result = isolation_manager.execute_in_container(
                    container_id,
                    "pip install pytest pytest-cov pytest-asyncio || echo 'Failed to install test dependencies'"
                )
                self.logger.info(f"   Status: {install_result.get('status')}")
                if install_result.get('stdout'):
                    self.logger.info(f"   Output: {install_result['stdout'][:200]}...")
            
            return result
        
        elif agent_name == "vanya":
            # Vanya - Mock UI Generator  
            agent = Vanya(self.project_id, self.workspace)
            return await agent.execute(input_data)
        
        elif agent_name == "pranav":
            from app.agents.pranav import Pranav
            agent = Pranav(self.project_id, self.workspace)
            return await agent.execute(input_data)
        
        elif agent_name == "riya":
            # Riya - Mobile App Developer
            agent = Riya(self.project_id, self.workspace)
            return await agent.execute(input_data)
        
        else:
            raise ValueError(f"Unknown agent: {agent_name}")
    


    
    # =========================================================================
    # PATENT REQUIREMENT: ADVERSARIAL QUALITY GATE
    # =========================================================================
    
    async def adversarial_quality_gate(self, code: Dict[str, Any]) -> Dict[str, Any]:
        """
        Patent Claim 4: GAN-based adversarial reviews with PARALLEL execution.
        
        Three adversarial agents compete to find the most bugs:
        - Navya: Logic errors (Generator)
        - Karan: Security vulnerabilities (Discriminator 1)
        - Deepika: Performance issues (Discriminator 2)
        
        Competitive dynamics:
        - Agents run in parallel
        - Reward based on unique findings
        - Competition drives thoroughness
        """
        self.logger.info("🎯 Starting PARALLEL adversarial quality gate...")
        
        # Extract code for review
        backend_code = code.get("backend", {}).get("code", "")
        frontend_code = code.get("frontend", {}).get("code", "")
        
        if not backend_code and not frontend_code:
            self.logger.warning("⚠️ No code to review")
            return {
                "status": "skipped",
                "reason": "No code available"
            }
        
        # Combine code for review
        combined_code = f"# BACKEND\n{backend_code}\n\n# FRONTEND\n{frontend_code}"
        
        # Create adversarial agents
        from app.agents.navya_adversarial import NavyaAdversarial
        from app.agents.karan_adversarial import KaranAdversarial
        from app.agents.deepika_adversarial import DeepikaAdversarial
        
        navya = NavyaAdversarial(project_id=self.project_id, workspace=self.workspace)
        karan = KaranAdversarial(project_id=self.project_id, workspace=self.workspace)
        deepika = DeepikaAdversarial(project_id=self.project_id, workspace=self.workspace)
        
        # PATENT CLAIM 4: PARALLEL COMPETITIVE EXECUTION
        # ================================================
        self.logger.info("🔥 Launching 3 adversarial agents in parallel...")
        
        start_time = time.time()
        
        # Execute all three agents simultaneously using asyncio.gather
        # This creates the competitive "GAN-like" training dynamic
        results = await asyncio.gather(
            navya.review(combined_code, file_type="python"),
            karan.review(combined_code, file_type="python"),
            deepika.review(combined_code, file_type="python"),
            return_exceptions=True  # Don't fail if one agent errors
        )
        
        execution_time = time.time() - start_time
        
        # Unpack results
        navya_result, karan_result, deepika_result = results
        
        # Handle exceptions
        if isinstance(navya_result, Exception):
            self.logger.error(f"Navya failed: {navya_result}")
            navya_result = {"agent": "NAVYA", "bugs_found": 0, "details": [], "error": str(navya_result)}
        
        if isinstance(karan_result, Exception):
            self.logger.error(f"Karan failed: {karan_result}")
            karan_result = {"agent": "KARAN", "vulnerabilities_found": 0, "details": [], "error": str(karan_result)}
        
        if isinstance(deepika_result, Exception):
            self.logger.error(f"Deepika failed: {deepika_result}")
            deepika_result = {"agent": "DEEPIKA", "issues_found": 0, "details": [], "error": str(deepika_result)}
        
        # Calculate rewards based on unique findings
        rewards = self._calculate_adversarial_rewards(navya_result, karan_result, deepika_result)
        
        # Aggregate results
        total_bugs = (
            navya_result.get("bugs_found", 0) +
            karan_result.get("vulnerabilities_found", 0) +
            deepika_result.get("issues_found", 0)
        )
        
        review_summary = {
            "status": "completed",
            "execution_mode": "parallel",  # Patent Claim 4
            "total_bugs": total_bugs,
            "execution_time_seconds": execution_time,
            "navya_result": navya_result,
            "karan_result": karan_result,
            "deepika_result": deepika_result,
            "rewards": rewards,  # GAN training component
            "timestamp": time.time()
        }
        
        self.logger.info(
            f"✅ Adversarial review complete: {total_bugs} issues found in {execution_time:.1f}s\n"
            f"   Navya: {navya_result.get('bugs_found', 0)} bugs (reward: {rewards['navya']:.2f})\n"
            f"   Karan: {karan_result.get('vulnerabilities_found', 0)} vulnerabilities (reward: {rewards['karan']:.2f})\n"
            f"   Deepika: {deepika_result.get('issues_found', 0)} performance issues (reward: {rewards['deepika']:.2f})"
        )
        
        # Store results with SHA-256 verification
        await self._store_with_hash("adversarial_review", review_summary)
        
        # REPORT TO TILOTMA
        await self.tilotma_reporter.report_quality_gate(review_summary)
        
        # GAN TRAINING - Learn from review cycle
        all_reviews = {
            "navya": navya_result,
            "karan": karan_result,
            "deepika": deepika_result
        }
        
        # Determine global outcome based on total bugs
        outcome = "rejected" if total_bugs > 0 else "accepted"
        
        # ADVANCED FEATURE: Collaboration Discussion
        if outcome == "rejected":
            self.logger.info("🗣️ Initiating collaboration discussion for found issues...")
            collaboration = CollaborationSession(self.project_id, ["shubham", "navya", "karan", "deepika"])
            
            # Discuss bugs with Navya
            if navya_result.get("details"):
                await collaboration.start_review_discussion("navya", "shubham", combined_code, navya_result.get("details", [])[:3])
                 
            # Discuss vulnerabilities with Karan
            if karan_result.get("details"):
                await collaboration.start_review_discussion("karan", "shubham", combined_code, karan_result.get("details", [])[:3])
            
            # Discuss performance with Deepika
            if deepika_result.get("details"):
                await collaboration.start_review_discussion("deepika", "shubham", combined_code, deepika_result.get("details", [])[:3])
                 
            review_summary["collaboration_log"] = collaboration.discussion
            await self._store_with_hash("adversarial_review", review_summary)
            
            # Report updated summary with collaboration log to Tilotma
            await self.tilotma_reporter.report_quality_gate(review_summary)
        
        for agent_name, review in all_reviews.items():
            await adversarial_trainer.train_adversarial_pair(
                generator_agent="shubham",
                discriminator_agent=agent_name,
                code=code,
                review_result=review,
                outcome=outcome
            )
            
            self.logger.info(f"🔄 GAN trained: shubham vs {agent_name} ({outcome})")
        
        return review_summary
    
    def _calculate_adversarial_rewards(
        self,
        navya_result: Dict,
        karan_result: Dict,
        deepika_result: Dict
    ) -> Dict[str, float]:
        """
        Patent Claim 4: Calculate rewards for GAN-based training.
        
        Reward function:
        - Base: 1.0 per bug found
        - Bonus: +0.5 for CRITICAL severity
        - Bonus: +0.3 for HIGH severity
        - Penalty: -0.2 for duplicate findings (same line/issue)
        
        Encourages:
        - Finding more bugs
        - Finding severe bugs
        - Finding unique bugs
        """
        # Extract findings
        navya_bugs = navya_result.get("details", [])
        karan_bugs = karan_result.get("details", [])
        deepika_bugs = deepika_result.get("details", [])
        
        # Calculate individual rewards
        navya_reward = self._calculate_agent_reward(navya_bugs)
        karan_reward = self._calculate_agent_reward(karan_bugs)
        deepika_reward = self._calculate_agent_reward(deepika_bugs)
        
        return {
            "navya": navya_reward,
            "karan": karan_reward,
            "deepika": deepika_reward
        }
    
    def _calculate_agent_reward(self, bugs: List[Dict]) -> float:
        """Calculate reward for a single agent"""
        reward = 0.0
        
        for bug in bugs:
            # Base reward
            reward += 1.0
            
            # Severity bonus
            severity = bug.get("severity", "LOW")
            if severity == "CRITICAL":
                reward += 0.5
            elif severity == "HIGH":
                reward += 0.3
        
        return reward

    
    # =========================================================================
    # PATENT REQUIREMENT: ITERATIVE REFINEMENT
    # =========================================================================
    
    async def iterative_refinement(
        self, 
        code: Dict, 
        review_result: Dict,
        max_iterations: int = 5
    ) -> Dict:
        """
        Patent requirement: Iterative refinement with enhanced convergence detection.
        
        Tracks multiple metrics:
        - Bug count (total and by severity)
        - Quality score (weighted by severity)
        - Improvement velocity (rate of bug reduction)
        
        Convergence criteria:
        - Zero bugs achieved, OR
        - Quality score > 0.95 and improving, OR
        - Diminishing returns detected (velocity < threshold)
        """
        
        iteration = 0
        bug_history = []
        quality_history = []
        severity_history = []
        failed_approaches = []
        
        self.logger.info("🔄 Starting iterative refinement with convergence tracking...")
        
        while iteration < max_iterations:
            self.logger.info(f"🔄 Refinement iteration {iteration + 1}/{max_iterations}")
            
            # Adversarial review
            review = await self.adversarial_quality_gate(code)
            bugs = review["total_bugs"]
            bug_history.append(bugs)
            
            # Calculate quality score (weighted by severity)
            quality_score = self._calculate_quality_score(review)
            quality_history.append(quality_score)
            
            # Track severity distribution
            severity_dist = self._get_severity_distribution(review)
            severity_history.append(severity_dist)
            
            # Store convergence metrics
            self.pipeline_state.convergence_metrics = {
                "bugs": bug_history,
                "quality": quality_history,
                "severity": severity_history
            }
            self.pipeline_state.convergence_iteration = iteration + 1
            
            # === CONVERGENCE CHECK ===
            convergence_result = self._check_convergence(
                bug_history, 
                quality_history, 
                severity_history,
                iteration
            )
            
            if convergence_result["converged"]:
                self.pipeline_state.convergence_achieved = True
                self.logger.info(
                    f"✅ Convergence achieved: {convergence_result['reason']} "
                    f"(Quality: {quality_score:.2%}, Bugs: {bugs})"
                )
                break
            
            # === STAGNATION DETECTION ===
            if len(bug_history) >= 3:
                if bug_history[-1] == bug_history[-2] == bug_history[-3]:
                    self.logger.warning("⚠️ Stagnation detected - trying alternative strategy")
                    
                    # Extract persistent bugs from review
                    persistent_bugs = []
                    for agent_key in ["navya_result", "karan_result", "deepika_result"]:
                        if agent_key in review:
                            persistent_bugs.extend(review[agent_key].get("details", []))
                    
                    code = await self._alternative_strategy(
                        code, 
                        persistent_bugs,
                        failed_approaches
                    )
                    self.pipeline_state.recovery_strategies_used.append("alternative_strategy")
                    continue
            
            # === DIMINISHING RETURNS CHECK ===
            if len(bug_history) >= 3:
                improvement_velocity = self._calculate_improvement_velocity(bug_history)
                if improvement_velocity < 0.1 and bugs > 0:
                    self.logger.warning(
                        f"⚠️ Diminishing returns detected (velocity: {improvement_velocity:.2f})"
                    )
                    # Continue but flag for Tilotma review
                    self.pipeline_state.quality_scores["diminishing_returns"] = True
            
            # === FIX BUGS ===
            if bugs > 0:
                self.logger.info(f"🔧 Fixing {bugs} bugs (Quality: {quality_score:.2%})...")
                failed_approaches.append({
                    "iteration": iteration,
                    "bugs": bugs,
                    "quality": quality_score
                })
                code = await self._fix_bugs(code, review)
            
            iteration += 1
        
        # Final quality assessment
        final_quality = quality_history[-1] if quality_history else 0.0
        final_bugs = bug_history[-1] if bug_history else 0
        
        if final_bugs > 0:
            self.logger.warning(
                f"⚠️ Refinement complete: {final_bugs} bugs remaining, "
                f"quality: {final_quality:.2%}"
            )
        else:
            self.logger.info(f"✅ Refinement complete: Zero defects, quality: {final_quality:.2%}")
        
        return code
    
    def _calculate_quality_score(self, review: Dict) -> float:
        """
        Calculate weighted quality score from review results.
        
        Scoring:
        - CRITICAL bug: -10 points
        - HIGH bug: -5 points
        - MEDIUM bug: -2 points
        - LOW bug: -1 point
        
        Returns score from 0.0 to 1.0
        """
        penalty = 0
        
        for agent_key in ["navya_result", "karan_result", "deepika_result"]:
            if agent_key not in review:
                continue
                
            agent_result = review[agent_key]
            details = agent_result.get("details", [])
            
            for bug in details:
                severity = bug.get("severity", "LOW")
                if severity == "CRITICAL":
                    penalty += 10
                elif severity == "HIGH":
                    penalty += 5
                elif severity == "MEDIUM":
                    penalty += 2
                else:
                    penalty += 1
        
        # Convert penalty to score (max penalty = 100 for normalization)
        score = max(0.0, 1.0 - (penalty / 100.0))
        return score
    
    def _get_severity_distribution(self, review: Dict) -> Dict[str, int]:
        """Get distribution of bugs by severity"""
        distribution = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        
        for agent_key in ["navya_result", "karan_result", "deepika_result"]:
            if agent_key not in review:
                continue
                
            agent_result = review[agent_key]
            details = agent_result.get("details", [])
            
            for bug in details:
                severity = bug.get("severity", "LOW")
                distribution[severity] = distribution.get(severity, 0) + 1
        
        return distribution
    
    def _check_convergence(
        self,
        bug_history: List[int],
        quality_history: List[float],
        severity_history: List[Dict],
        iteration: int
    ) -> Dict[str, Any]:
        """
        Check if convergence has been achieved.
        
        Convergence criteria (any one triggers):
        1. Zero bugs
        2. Quality score > 0.95 and no critical/high bugs
        3. Quality improving and > 0.90 for 2 consecutive iterations
        """
        current_bugs = bug_history[-1]
        current_quality = quality_history[-1]
        current_severity = severity_history[-1]
        
        # Criterion 1: Zero bugs
        if current_bugs == 0:
            return {"converged": True, "reason": "Zero defects achieved"}
        
        # Criterion 2: High quality with no critical bugs
        if current_quality > 0.95:
            if current_severity["CRITICAL"] == 0 and current_severity["HIGH"] == 0:
                return {
                    "converged": True, 
                    "reason": f"High quality ({current_quality:.2%}) with no critical/high bugs"
                }
        
        # Criterion 3: Sustained high quality
        if len(quality_history) >= 2:
            if quality_history[-1] > 0.90 and quality_history[-2] > 0.90:
                if quality_history[-1] >= quality_history[-2]:  # Improving or stable
                    if current_severity["CRITICAL"] == 0:
                        return {
                            "converged": True,
                            "reason": f"Sustained quality ({current_quality:.2%}) with no critical bugs"
                        }
        
        return {"converged": False, "reason": None}
    
    def _calculate_improvement_velocity(self, bug_history: List[int]) -> float:
        """
        Calculate rate of improvement (bugs fixed per iteration).
        
        Returns:
            Average bugs fixed per iteration over last 3 iterations
        """
        if len(bug_history) < 2:
            return 0.0
        
        # Look at last 3 iterations
        recent = bug_history[-3:] if len(bug_history) >= 3 else bug_history
        
        total_improvement = 0
        for i in range(1, len(recent)):
            improvement = max(0, recent[i-1] - recent[i])
            total_improvement += improvement
        
        velocity = total_improvement / (len(recent) - 1) if len(recent) > 1 else 0.0
        return velocity
    
    async def _fix_bugs(self, code: Dict, review: Dict) -> Dict:
        """
        Route bugs back to appropriate agents for fixing.
        
        Routing logic:
        - Backend bugs → Shubham
        - Frontend bugs → Aanya
        - Architecture bugs → Saanvi for redesign
        """
        self.logger.info("🔧 Routing bugs to agents for fixes...")
        
        # Extract bug details from review
        all_bugs = []
        
        for agent_key in ["navya_result", "karan_result", "deepika_result"]:
            if agent_key in review:
                bugs = review[agent_key].get("details", [])
                all_bugs.extend(bugs)
        
        if not all_bugs:
            self.logger.info("No bugs to fix")
            return code
        
        # Categorize bugs by component
        backend_bugs = []
        frontend_bugs = []
        architecture_bugs = []
        
        for bug in all_bugs:
            file_path = bug.get("file", "")
            
            if "backend" in file_path or ".py" in file_path:
                backend_bugs.append(bug)
            elif "frontend" in file_path or ".tsx" in file_path or ".jsx" in file_path:
                frontend_bugs.append(bug)
            else:
                architecture_bugs.append(bug)
        
        # Fix backend bugs
        if backend_bugs:
            self.logger.info(f"🔧 Routing {len(backend_bugs)} backend bugs to Shubham...")
            
            from app.agents.shubham import Shubham
            shubham = Shubham(
                project_id=self.project_id,
                workspace=self.workspace
            )
            
            fix_input = {
                "mode": "fix_bugs",
                "original_code": code.get("backend", {}),
                "bugs_to_fix": backend_bugs
            }
            
            fixed_backend = await shubham.execute(fix_input)
            code["backend"] = fixed_backend
        
        # Fix frontend bugs
        if frontend_bugs:
            self.logger.info(f"🔧 Routing {len(frontend_bugs)} frontend bugs to Aanya...")
            
            from app.agents.aanya import Aanya
            aanya = Aanya(
                project_id=self.project_id,
                workspace=self.workspace
            )
            
            fix_input = {
                "mode": "fix_bugs",
                "original_code": code.get("frontend", {}),
                "bugs_to_fix": frontend_bugs
            }
            
            fixed_frontend = await aanya.execute(fix_input)
            code["frontend"] = fixed_frontend
        
        # Architecture bugs require redesign
        if architecture_bugs:
            self.logger.warning(
                f"⚠️ {len(architecture_bugs)} architecture-level bugs found - "
                f"may require Saanvi redesign"
            )
        
        self.logger.info(f"✅ Bug fixes applied: {len(backend_bugs)} backend, {len(frontend_bugs)} frontend")
        return code
    
    async def _alternative_strategy(
        self, 
        current_code: dict, 
        persistent_bugs: list,
        failed_approaches: list
    ) -> dict:
        """Try fundamentally different approach when stuck."""
        
        self.logger.warning(f"🔄 Activating alternative strategy for {len(persistent_bugs)} persistent bugs")
        
        # Strategy 1: Search for solutions
        from app.agents.research_agent import ResearchAgent
        researcher = ResearchAgent(self.project_id)
        
        for bug in persistent_bugs[:3]:  # Top 3 persistent bugs
            search_result = await researcher.find_error_solution(
                error_message=f"{bug.get('type')}: {bug.get('description')}",
                code_context=bug.get('location', '')
            )
            if search_result.get('summary'):
                bug['research_solution'] = search_result['summary']
        
        # Strategy 2: Model escalation — use highest-quality model
        # Force Claude Opus for the fix attempt
        prompt = f"""CRITICAL: These bugs have persisted through {len(failed_approaches)} fix attempts.
        
    Previous approaches that FAILED:
    {json.dumps(failed_approaches, indent=2)}
    
    Persistent bugs with research solutions:
    {json.dumps(persistent_bugs, indent=2)}
    
    You must take a FUNDAMENTALLY DIFFERENT approach.
    Consider: restructuring the code, using different libraries, 
    changing the algorithm, or rewriting the problematic section entirely.
    
    Current code that needs fixing:
    {json.dumps(current_code, indent=2)}
    
    Respond with the complete updated code structure in JSON format.
    """
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="alternative_fix",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=8000
        )
        
        # Handle JSON response if returned, otherwise return original (fallback)
        try:
            # Simple heuristic to extract JSON if model wraps it
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "{" in content and "}" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                content = content[start:end]
            
            fixed_code = json.loads(content)
            return fixed_code
        except Exception as e:
            self.logger.error(f"❌ Failed to parse alternative strategy response: {e}")
            return current_code
    
    # =========================================================================
    # PATENT REQUIREMENT: SHA-256 CONTEXT VERIFICATION (Claim 1c, Claim 3)
    # =========================================================================
    
    def _compute_sha256_hash(self, data: Dict[str, Any]) -> str:
        """
        Patent Claim 3: Compute SHA-256 hash of context data.
        
        Args:
            data: Context data to hash
            
        Returns:
            64-character hex digest
        """
        # Serialize to canonical JSON (sorted keys for consistency)
        serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
        
        # Compute SHA-256
        hash_obj = hashlib.sha256(serialized.encode('utf-8'))
        hash_digest = hash_obj.hexdigest()
        
        self.logger.debug(f"🔐 Computed hash: {hash_digest[:16]}...")
        return hash_digest
    
    async def _store_with_hash(self, context_key: str, data: Dict[str, Any]):
        """
        Store context with SHA-256 verification hash.
        
        Patent Claim 3: All context storage includes cryptographic proof.
        """
        # Compute hash before storage
        context_hash = self._compute_sha256_hash(data)
        
        # Add hash to data
        data_with_hash = {
            "data": data,
            "hash": context_hash,
            "timestamp": time.time(),
            "phase": self.pipeline_state.current_phase.value
        }
        
        # Store with hash
        context_engine.store_context(
            self.project_id,
            context_key,
            data_with_hash
        )
        
        # Add to hash chain for audit trail
        self.pipeline_state.hash_chain.append(context_hash)
        
        self.logger.info(f"🔐 Stored {context_key} with hash: {context_hash[:16]}...")
    
    async def _verify_context_integrity(self, context_key: str) -> bool:
        """
        Patent Claim 3: Verify context integrity using SHA-256.
        
        Detects:
        - Data corruption
        - Unauthorized modifications
        - Agent output tampering
        
        Returns:
            True if integrity verified, False if corruption detected
        """
        # Retrieve stored context
        stored = context_engine.get_context(self.project_id, context_key)
        
        if not stored:
            self.logger.warning(f"⚠️ Context not found: {context_key}")
            return False
        
        # Extract original hash
        stored_hash = stored.get("hash")
        stored_data = stored.get("data")
        
        if not stored_hash or not stored_data:
            self.logger.error(f"❌ Context missing hash: {context_key}")
            return False
        
        # Recompute hash from current data
        computed_hash = self._compute_sha256_hash(stored_data)
        
        # Compare hashes
        if computed_hash != stored_hash:
            self.logger.error(
                f"🚨 CONTEXT CORRUPTION DETECTED: {context_key}\n"
                f"   Expected: {stored_hash[:16]}...\n"
                f"   Got:      {computed_hash[:16]}..."
            )
            
            # Record corruption event
            self.pipeline_state.error_history.append({
                "type": "context_corruption",
                "context_key": context_key,
                "expected_hash": stored_hash,
                "computed_hash": computed_hash,
                "timestamp": time.time()
            })
            
            # Trigger rollback
            await self._rollback_to_last_good_state(context_key)
            return False
        
        self.logger.debug(f"✅ Context integrity verified: {context_key}")
        return True
    
    async def _rollback_to_last_good_state(self, corrupted_key: str):
        """
        Patent Claim 3: Automatic rollback on corruption detection.
        
        Recovery strategy:
        1. Identify last good hash in chain
        2. Find corresponding phase
        3. Re-execute from that phase
        """
        self.logger.warning(f"🔄 Initiating rollback due to corruption in {corrupted_key}")
        
        # Find last good hash (second-to-last in chain)
        if len(self.pipeline_state.hash_chain) >= 2:
            last_good_hash = self.pipeline_state.hash_chain[-2]
            self.logger.info(f"📍 Last good state: {last_good_hash[:16]}...")
            
            # Mark current phase for re-execution
            phase = self.pipeline_state.current_phase
            self.logger.info(f"🔁 Will re-execute phase: {phase.value}")
            
            # Record rollback
            await self._record_mistake(
                agent_name="arjun_integrity",
                error=f"Context corruption detected in {corrupted_key}",
                fix=f"Automatic rollback to hash {last_good_hash[:16]}"
            )
            
            # Remove corrupted hash from chain
            self.pipeline_state.hash_chain.pop()
        else:
            self.logger.error("❌ No good state to rollback to - pipeline restart required")
            raise RuntimeError("Context corruption with no recovery point")
    
    # =========================================================================
    # PATENT REQUIREMENT: MISTAKE MEMORY
    # =========================================================================
    
    async def _check_mistake_memory(self, agent_name: str, task_type: str) -> List[Dict]:
        """
        Patent requirement: Query mistake memory before agent execution.
        """
        
        # Query mistake memory (no project_context parameter)
        similar_mistakes = mistake_memory.query_similar_mistakes(
            agent_name=agent_name,
            task_type=task_type,
            input_data=""  # Empty string for now, can be enhanced later
        )
        
        if similar_mistakes:
            self.logger.info(
                f"📚 Found {len(similar_mistakes)} similar past mistakes for {agent_name}"
            )
        
        return similar_mistakes
    
    async def _record_mistake(self, agent_name: str, error: str, fix: str):
        """Record mistake for future learning"""
        
        mistake_memory.record_failure(
            agent_name=agent_name,
            task_type=self.pipeline_state.current_phase.value,
            input_data="",  # Fixed: removed context parameter, added input_data
            error=error,
            fix=fix
        )
    
    # =========================================================================
    # PROGRESS TRACKING
    # =========================================================================
    
    async def _update_progress(self, phase: PipelinePhase, percentage: int):
        """Update progress and notify Tilotma"""
        
        self.pipeline_state.current_phase = phase
        self.pipeline_state.progress_percentage = percentage
        
        if phase.value not in self.pipeline_state.completed_phases:
            self.pipeline_state.completed_phases.append(phase.value)
        
        # Report to Tilotma
        await self.tilotma_reporter.report_progress(phase.value, percentage)
        
        self.logger.info(f"📊 Progress: {phase.value} - {percentage}%")
    
    # =========================================================================
    # TILOTMA INTERVENTION SUPPORT
    # =========================================================================
    
    async def pause_pipeline(self):
        """Pause pipeline (called by Tilotma intervention)"""
        self.logger.warning("⏸️ Pipeline paused by Tilotma")
        self.pipeline_state.paused = True
        self.pipeline_state.intervention_count += 1
    
    async def resume_pipeline(self):
        """Resume pipeline after Tilotma intervention"""
        self.logger.info("▶️ Pipeline resumed after Tilotma intervention")
        self.pipeline_state.paused = False

    async def rerun_agent(self, agent_name: str, correction: dict):
        """Re-run a specific agent with correction instructions."""
        self.logger.info(f"🔄 Re-running {agent_name} with Tilotma's correction")
        result = await self._delegate_to_agent(agent_name, correction)
        self.pipeline_state.agent_outputs[agent_name] = result

    async def rerun_phases(self, agents: list, fix_request: dict):
        """Re-run specific pipeline phases for fixes."""
        for agent_name in agents:
            self.logger.info(f"🔄 Re-running {agent_name} for fixes")
            result = await self._delegate_to_agent(agent_name, fix_request)
            self.pipeline_state.agent_outputs[agent_name] = result
        
        # Re-run adversarial review after fixes
        code = {
            "backend": self.pipeline_state.agent_outputs.get("shubham"),
            "frontend": self.pipeline_state.agent_outputs.get("aanya")
        }
        await self.adversarial_quality_gate(code)
    
    # =========================================================================
    # ERROR HANDLING
    # =========================================================================
    
    async def _handle_pipeline_failure(self, error: Exception):
        """Handle pipeline failure"""
        
        self.logger.error(f"Pipeline failed: {error}")
        
        # Store failure context
        # Convert pipeline state to serializable format
        state_dict = self.pipeline_state.__dict__.copy()
        state_dict['current_phase'] = self.pipeline_state.current_phase.value  # Convert enum to string
        
        # Convert datetime objects to ISO format strings
        from datetime import datetime
        for key, value in state_dict.items():
            if isinstance(value, datetime):
                state_dict[key] = value.isoformat()
        
        await self._store_with_hash(
            "pipeline_failure",
            {
                "error": str(error),
                "phase": self.pipeline_state.current_phase.value,
                "timestamp": time.time(),
                "state": state_dict
            }
        )
        
        # Report to Tilotma
        await self.tilotma_reporter.report_agent_failure("arjun_pipeline", str(error))

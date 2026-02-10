# NexSidi Agent Collaboration Fix Plan
**Version:** 2.0  
**Date:** February 10, 2026  
**Executor:** Google Antigravity Agent  
**Estimated Time:** 4-6 hours  
**Priority:** CRITICAL

---

## 🎯 OBJECTIVE

Fix the broken agent collaboration system so that:
1. Tilotma → Arjun handoff works reliably
2. Arjun → Specialized Agents data flow is correct
3. Generated code is actually written to disk
4. Files are deployed to GCP Cloud Run
5. User receives working application URLs

---

## 🔴 CRITICAL BUGS IDENTIFIED

### BUG #1: Tilotma Never Starts Project Execution
**File:** `app/agents/tilotma.py`  
**Line:** ~250-300 (chat method)  
**Severity:** CRITICAL

**Current Behavior:**
```python
async def chat(self, user_message: str) -> str:
    # Tilotma gathers requirements
    response = await self._analyze_message(user_message)
    
    # BUT NEVER CALLS: await self._handoff_to_arjun()
    # User waits forever, nothing happens!
    
    return response  # Just returns chat text
```

**Required Behavior:**
```python
async def chat(self, user_message: str) -> Dict:
    """
    Chat with user and automatically start project when ready
    """
    # Add message to memory
    self.memory.add_message(user_message)
    
    # Check if requirements are complete
    requirements_complete = await self._check_requirements_complete()
    
    if requirements_complete:
        # START THE PROJECT!
        self.logger.info("✅ Requirements complete - Starting project")
        return await self._handoff_to_arjun()
    
    # Otherwise continue gathering requirements
    response = await self._analyze_message(user_message)
    return {
        "type": "chat_response",
        "message": response,
        "requirements_status": "gathering"
    }

async def _check_requirements_complete(self) -> bool:
    """
    Check if we have enough information to start development
    """
    understanding = self.memory.get_understanding()
    
    # Must have minimum requirements:
    # 1. Project type (blog, e-commerce, etc.)
    # 2. At least 3 key features
    # 3. User has confirmed understanding
    
    has_project_type = understanding.project_type is not None
    has_features = len(understanding.key_features) >= 3
    user_confirmed = understanding.user_confirmed == True
    
    return has_project_type and has_features and user_confirmed

async def _handoff_to_arjun(self) -> Dict:
    """
    Hand off to Arjun for project execution
    """
    self.logger.info("🚀 Handing off to Arjun (Project Manager)")
    
    # Get structured requirements from memory
    requirements = {
        "project_id": self.project_id,
        "user_id": self.user_id,
        "project_type": self.memory.project_understanding.project_type,
        "description": self.memory.project_understanding.description,
        "key_features": self.memory.project_understanding.key_features,
        "tech_preferences": self.memory.project_understanding.tech_preferences,
        "conversation_history": self.memory.get_conversation_summary()
    }
    
    # Persist handoff state (for crash recovery)
    await context_engine.store(
        f"project:{self.project_id}:handoff",
        {
            "status": "handoff_initiated",
            "from_agent": "tilotma",
            "to_agent": "arjun",
            "requirements": requirements,
            "timestamp": datetime.now().isoformat()
        }
    )
    
    # Create Arjun instance
    from app.agents.arjun import Arjun
    self.arjun = Arjun(self.project_id, self.user_id)
    
    # Start pipeline in background task
    task = asyncio.create_task(
        self._execute_arjun_pipeline(requirements)
    )
    
    # Store task reference
    self.arjun_task = task
    
    return {
        "type": "project_started",
        "message": "✅ Project started! Your development team is now working on it.",
        "project_id": self.project_id,
        "status": "in_progress"
    }

async def _execute_arjun_pipeline(self, requirements: Dict):
    """
    Execute Arjun's pipeline and handle errors
    """
    try:
        result = await self.arjun.execute_pipeline(requirements)
        
        # Update handoff state
        await context_engine.store(
            f"project:{self.project_id}:handoff",
            {
                "status": "completed",
                "result": result,
                "timestamp": datetime.now().isoformat()
            }
        )
        
        self.logger.info("✅ Pipeline completed successfully")
        
    except Exception as e:
        self.logger.error(f"❌ Pipeline failed: {e}")
        
        # Update handoff state with error
        await context_engine.store(
            f"project:{self.project_id}:handoff",
            {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Notify user via WebSocket
        # (Implementation depends on your WebSocket setup)
```

---

### BUG #2: Agent Data Format Mismatch
**Files:** `app/agents/arjun.py`, `app/agents/saanvi.py`, `app/agents/shubham.py`  
**Severity:** CRITICAL

**Current Problem:**
```python
# In arjun.py
saanvi_result = await self._delegate_to_agent("saanvi", requirements)
# saanvi_result might be: RequirementsAnalysisResult object

shubham_result = await self._delegate_to_agent("shubham", saanvi_result)
# But Shubham expects: Dict with "architecture" key!
# MISMATCH = Pipeline breaks
```

**Required Fix:**

**Step 1: Define Clear Data Contracts**

Create new file: `app/agents/contracts.py`

```python
"""
Data contracts between agents
Ensures type safety and clear expectations
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from enum import Enum


class ProjectType(Enum):
    LANDING_PAGE = "landing_page"
    BLOG = "blog"
    ECOMMERCE = "ecommerce"
    SAAS = "saas"
    SOCIAL = "social"


@dataclass
class TilotmaOutput:
    """What Tilotma gives to Arjun"""
    project_id: str
    user_id: str
    project_type: ProjectType
    description: str
    key_features: List[str]
    tech_preferences: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SaanviOutput:
    """What Saanvi gives to Vikram/Shubham"""
    project_id: str
    requirements: Dict  # Structured requirements
    complexity_score: int  # 1-10
    estimated_cost: int  # In INR
    estimated_hours: int
    recommended_tech_stack: Dict
    database_requirements: List[str]
    api_endpoints_needed: List[str]
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ShubhamInput:
    """What Shubham needs to generate backend"""
    project_id: str
    architecture: Dict  # From Saanvi/Vikram
    tech_stack: str  # "fastapi", "express-ts", etc.
    database_schema: Dict
    api_endpoints: List[Dict]
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ShubhamOutput:
    """What Shubham produces"""
    project_id: str
    files_generated: List[str]  # Paths to generated files
    files_written: bool  # Were they actually saved?
    workspace_path: str
    backend_framework: str
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AanyaInput:
    """What Aanya needs to generate frontend"""
    project_id: str
    api_base_url: str  # Backend URL
    api_endpoints: List[Dict]
    design_system: Dict
    tech_stack: str  # "react-vite-ts", "nextjs-ts", etc.
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AanyaOutput:
    """What Aanya produces"""
    project_id: str
    files_generated: List[str]
    files_written: bool
    workspace_path: str
    frontend_framework: str
    
    def to_dict(self) -> Dict:
        return asdict(self)
```

**Step 2: Update Arjun to Use Contracts**

In `app/agents/arjun.py`, modify `execute_pipeline`:

```python
from app.agents.contracts import (
    TilotmaOutput, SaanviOutput, ShubhamInput, 
    ShubhamOutput, AanyaInput, AanyaOutput
)

async def execute_pipeline(self, requirements: Dict) -> PipelineResult:
    """
    Execute full SDLC pipeline with type-safe data passing
    """
    try:
        self.logger.info("🚀 Starting SDLC pipeline execution...")
        self.pipeline_state.start_time = datetime.now()
        
        # Parse input from Tilotma
        tilotma_output = TilotmaOutput(**requirements)
        
        # Phase 1: Requirements Analysis (Saanvi)
        await self._update_progress(PipelinePhase.REQUIREMENTS, 10)
        self.logger.info("📋 Phase 1: Requirements Analysis (Saanvi)")
        
        saanvi_result = await self._delegate_to_agent(
            "saanvi", 
            tilotma_output.to_dict()
        )
        
        # Convert to contract
        saanvi_output = SaanviOutput(**saanvi_result)
        self.pipeline_state.agent_outputs["saanvi"] = saanvi_output.to_dict()
        
        # Phase 2: Backend Development (Shubham)
        await self._update_progress(PipelinePhase.DEVELOPMENT, 30)
        self.logger.info("🔧 Phase 2: Backend Development (Shubham)")
        
        # Create structured input for Shubham
        shubham_input = ShubhamInput(
            project_id=self.project_id,
            architecture=saanvi_output.requirements,
            tech_stack=saanvi_output.recommended_tech_stack.get("backend", "fastapi"),
            database_schema={},  # Will be designed by Vikram later
            api_endpoints=saanvi_output.api_endpoints_needed
        )
        
        shubham_result = await self._delegate_to_agent(
            "shubham",
            shubham_input.to_dict()
        )
        
        # Convert and validate
        shubham_output = ShubhamOutput(**shubham_result)
        
        # CRITICAL CHECK: Were files actually written?
        if not shubham_output.files_written:
            raise ValueError("❌ Shubham generated code but did not write files!")
        
        self.pipeline_state.agent_outputs["shubham"] = shubham_output.to_dict()
        
        # Phase 3: Frontend Development (Aanya)
        await self._update_progress(PipelinePhase.DEVELOPMENT, 50)
        self.logger.info("🎨 Phase 3: Frontend Development (Aanya)")
        
        aanya_input = AanyaInput(
            project_id=self.project_id,
            api_base_url="http://localhost:8000",  # Will be updated after deployment
            api_endpoints=saanvi_output.api_endpoints_needed,
            design_system={},  # From Vanya
            tech_stack=saanvi_output.recommended_tech_stack.get("frontend", "nextjs-ts")
        )
        
        aanya_result = await self._delegate_to_agent(
            "aanya",
            aanya_input.to_dict()
        )
        
        aanya_output = AanyaOutput(**aanya_result)
        
        # CRITICAL CHECK
        if not aanya_output.files_written:
            raise ValueError("❌ Aanya generated code but did not write files!")
        
        self.pipeline_state.agent_outputs["aanya"] = aanya_output.to_dict()
        
        # Phase 4: QA Testing (Adversarial Agents)
        await self._update_progress(PipelinePhase.QA_TESTING, 70)
        self.logger.info("🧪 Phase 4: QA Testing (Navya, Karan, Deepika)")
        
        # Run QA in parallel
        qa_results = await self._run_adversarial_qa({
            "backend": shubham_output.to_dict(),
            "frontend": aanya_output.to_dict()
        })
        
        # Phase 5: Deployment (Pranav)
        await self._update_progress(PipelinePhase.DEPLOYMENT, 90)
        self.logger.info("🚀 Phase 5: Deployment (Pranav)")
        
        deployment_result = await self._delegate_to_agent(
            "pranav",
            {
                "backend_path": shubham_output.workspace_path,
                "frontend_path": aanya_output.workspace_path,
                "project_id": self.project_id
            }
        )
        
        # CRITICAL CHECK: Are URLs live?
        if not deployment_result.get("backend_url") or not deployment_result.get("frontend_url"):
            raise ValueError("❌ Deployment did not produce live URLs!")
        
        self.pipeline_state.agent_outputs["pranav"] = deployment_result
        
        # Phase 6: Complete
        await self._update_progress(PipelinePhase.COMPLETED, 100)
        self.logger.info("✅ Pipeline completed successfully!")
        
        return PipelineResult(
            success=True,
            project_id=self.project_id,
            outputs=self.pipeline_state.agent_outputs,
            backend_url=deployment_result["backend_url"],
            frontend_url=deployment_result["frontend_url"]
        )
        
    except Exception as e:
        self.logger.error(f"❌ Pipeline failed: {e}")
        await self._handle_pipeline_failure(e)
        raise
```

---

### BUG #3: Code Generated But Never Written to Disk
**File:** `app/agents/shubham.py`  
**Severity:** CRITICAL

**Current Problem:**
```python
async def generate_file(self, request: FileGenerationRequest):
    # Generates code content
    code = await ai_router.generate(prompt)
    
    # Returns string - NEVER WRITTEN TO DISK!
    return code
```

**Required Fix:**

In `app/agents/shubham.py`, update `generate_file` method:

```python
async def generate_file(self, request: FileGenerationRequest) -> Dict:
    """
    Generate code and WRITE TO DISK
    """
    self.logger.info(f"📝 Generating: {request.file_path}")
    
    # Generate code using AI
    code = await self._generate_code_content(request)
    
    # WRITE TO DISK
    full_path = os.path.join(
        self.workspace['code_dir'],
        request.file_path
    )
    
    # Create directories if needed
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    # Write file
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(code)
    
    self.logger.info(f"✅ Written: {full_path}")
    
    # Commit to git
    git_service.commit_agent_work(
        self.workspace['code_dir'],
        "shubham",
        f"Generated {request.file_path}"
    )
    
    # Verify file exists
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"❌ File was not written: {full_path}")
    
    return {
        "file_path": request.file_path,
        "full_path": full_path,
        "size_bytes": os.path.getsize(full_path),
        "status": "written"
    }

async def execute(self, input_data: Dict) -> Dict:
    """
    Execute backend generation and return structured output
    """
    # Generate all files
    files_generated = []
    
    for file_req in self._create_file_requests(input_data):
        result = await self.generate_file(file_req)
        files_generated.append(result)
    
    # Return structured output matching ShubhamOutput contract
    return {
        "project_id": self.project_id,
        "files_generated": [f["file_path"] for f in files_generated],
        "files_written": True,  # All files were written
        "workspace_path": self.workspace['code_dir'],
        "backend_framework": input_data.get("tech_stack", "fastapi")
    }
```

---

### BUG #4: Deployment Never Happens
**File:** `app/agents/pranav.py`, `app/agents/arjun.py`  
**Severity:** CRITICAL

**Current Problem:**
- Pranav exists and has deployment code
- But Arjun never calls Pranav!

**Required Fix:**

Already included in BUG #2 fix above (see Phase 5 in Arjun's execute_pipeline).

Verify `pranav.py` has this method:

```python
async def execute(self, input_data: Dict) -> Dict:
    """
    Deploy backend and frontend to GCP Cloud Run
    
    Args:
        input_data: {
            "backend_path": "/workspace/projects/proj-123/backend",
            "frontend_path": "/workspace/projects/proj-123/frontend",
            "project_id": "proj-123"
        }
    
    Returns:
        {
            "backend_url": "https://backend-proj123.run.app",
            "frontend_url": "https://frontend-proj123.run.app",
            "status": "deployed"
        }
    """
    self.logger.info("🚀 Starting deployment to GCP Cloud Run")
    
    # Deploy backend
    backend_url = await self._deploy_service(
        service_name=f"backend-{input_data['project_id']}",
        source_path=input_data['backend_path']
    )
    
    # Deploy frontend
    frontend_url = await self._deploy_service(
        service_name=f"frontend-{input_data['project_id']}",
        source_path=input_data['frontend_path']
    )
    
    # Verify URLs are live
    await self._verify_deployment(backend_url)
    await self._verify_deployment(frontend_url)
    
    return {
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "status": "deployed",
        "deployed_at": datetime.now().isoformat()
    }
```

---

### BUG #5: No Error Handling or Progress Updates
**Files:** All agent files  
**Severity:** HIGH

**Required Fix:**

Add WebSocket progress mixin to `app/agents/mixins.py`:

```python
class ProgressMixin:
    """
    Mixin for sending real-time progress updates via WebSocket
    """
    
    async def _send_progress(
        self, 
        message: str, 
        percentage: Optional[int] = None,
        status: str = "in_progress"
    ):
        """
        Send progress update to user via WebSocket
        """
        try:
            # Get WebSocket manager
            from app.api.websocket import manager
            
            # Send update
            await manager.broadcast_to_project(
                self.project_id,
                {
                    "type": "agent_progress",
                    "agent": self.__class__.__name__.lower(),
                    "message": message,
                    "percentage": percentage,
                    "status": status,
                    "timestamp": datetime.now().isoformat()
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to send progress: {e}")
```

Then update each agent to inherit this mixin and use it:

```python
class Shubham(ProgressMixin, MistakeMemoryMixin, ...):
    
    async def execute(self, input_data: Dict) -> Dict:
        await self._send_progress("🔧 Starting backend generation", 0)
        
        # Generate files...
        
        await self._send_progress("✅ Backend generation complete", 100, "success")
```

---

## 📋 IMPLEMENTATION CHECKLIST

### Phase 1: Data Contracts (30 mins)
- [ ] Create `app/agents/contracts.py` with all dataclasses
- [ ] Test imports in all agent files
- [ ] Verify no circular import issues

### Phase 2: Tilotma Fixes (1 hour)
- [ ] Add `_check_requirements_complete()` method
- [ ] Add `_handoff_to_arjun()` method
- [ ] Add `_execute_arjun_pipeline()` method
- [ ] Update `chat()` to use new flow
- [ ] Add error handling for background task

### Phase 3: Arjun Fixes (2 hours)
- [ ] Import contracts module
- [ ] Refactor `execute_pipeline()` to use contracts
- [ ] Add validation checks after each agent
- [ ] Add deployment phase (call Pranav)
- [ ] Add comprehensive error handling

### Phase 4: File Writing Fixes (1 hour)
- [ ] Update `shubham.py` `generate_file()` to write to disk
- [ ] Update `shubham.py` `execute()` to return structured output
- [ ] Do same for `aanya.py`
- [ ] Add file verification checks

### Phase 5: Deployment Fixes (30 mins)
- [ ] Verify `pranav.py` has `execute()` method
- [ ] Add URL verification in Pranav
- [ ] Test deployment locally

### Phase 6: Progress Updates (1 hour)
- [ ] Add `ProgressMixin` to `mixins.py`
- [ ] Update all agents to inherit mixin
- [ ] Add progress calls at key points
- [ ] Test WebSocket connection

### Phase 7: Integration Testing (1.5 hours)
- [ ] Create `tests/integration/test_full_pipeline.py`
- [ ] Test Tilotma → Arjun handoff
- [ ] Test Arjun → all agents flow
- [ ] Test file generation
- [ ] Test deployment
- [ ] Fix any issues found

---

## 🧪 VERIFICATION TESTS

Create file: `tests/integration/test_agent_collaboration.py`

```python
"""
Integration test for agent collaboration
"""
import pytest
import asyncio
from app.agents.tilotma import Tilotma
from app.agents.arjun import Arjun
from uuid import uuid4


@pytest.mark.asyncio
async def test_tilotma_to_arjun_handoff():
    """Test that Tilotma successfully hands off to Arjun"""
    
    project_id = str(uuid4())
    user_id = "test-user"
    
    # Create Tilotma
    tilotma = Tilotma(project_id, user_id)
    
    # Simulate requirements gathering
    messages = [
        "I want to build a blog",
        "It should have posts, comments, and user authentication",
        "Use Python FastAPI for backend",
        "Yes, that's correct, let's proceed"
    ]
    
    response = None
    for msg in messages:
        response = await tilotma.chat(msg)
    
    # Check that project was started
    assert response["type"] == "project_started"
    assert response["project_id"] == project_id
    
    # Check that handoff state was stored
    handoff_state = await context_engine.get(f"project:{project_id}:handoff")
    assert handoff_state["status"] == "handoff_initiated"


@pytest.mark.asyncio
async def test_arjun_pipeline_data_flow():
    """Test that data flows correctly between agents"""
    
    project_id = str(uuid4())
    user_id = "test-user"
    
    # Create Arjun
    arjun = Arjun(project_id, user_id)
    
    # Mock requirements
    requirements = {
        "project_id": project_id,
        "user_id": user_id,
        "project_type": "blog",
        "description": "A simple blog with posts and comments",
        "key_features": ["posts", "comments", "authentication"],
        "tech_preferences": {"backend": "fastapi"}
    }
    
    # Mock LLM calls (use cheap model or mock)
    # ... (implementation depends on your test setup)
    
    # Execute pipeline
    result = await arjun.execute_pipeline(requirements)
    
    # Verify outputs
    assert result.success == True
    assert "saanvi" in result.outputs
    assert "shubham" in result.outputs
    assert "aanya" in result.outputs
    assert "pranav" in result.outputs
    
    # Verify files were written
    shubham_output = result.outputs["shubham"]
    assert shubham_output["files_written"] == True
    assert len(shubham_output["files_generated"]) > 0
    
    # Verify deployment URLs
    assert result.backend_url.startswith("https://")
    assert result.frontend_url.startswith("https://")


@pytest.mark.asyncio
async def test_file_generation_writes_to_disk():
    """Test that Shubham actually writes files"""
    
    import os
    from app.agents.shubham import Shubham
    
    project_id = str(uuid4())
    workspace = workspace_manager.create_workspace(project_id)
    
    shubham = Shubham(project_id, workspace)
    
    # Generate a simple file
    file_req = FileGenerationRequest(
        file_path="test.py",
        file_type=FileType.MAIN,
        description="Test file"
    )
    
    result = await shubham.generate_file(file_req)
    
    # Verify file exists
    full_path = result["full_path"]
    assert os.path.exists(full_path)
    assert os.path.getsize(full_path) > 0
    
    # Cleanup
    os.remove(full_path)


@pytest.mark.asyncio
async def test_error_handling():
    """Test that errors are caught and handled properly"""
    
    project_id = str(uuid4())
    user_id = "test-user"
    
    arjun = Arjun(project_id, user_id)
    
    # Invalid requirements (missing required fields)
    bad_requirements = {
        "project_id": project_id
        # Missing other required fields!
    }
    
    # Should raise error, not crash
    with pytest.raises(Exception) as exc_info:
        await arjun.execute_pipeline(bad_requirements)
    
    # Verify error was logged
    handoff_state = await context_engine.get(f"project:{project_id}:handoff")
    assert handoff_state["status"] == "failed"
    assert "error" in handoff_state
```

---

## 🚀 EXECUTION ORDER

For Antigravity Agent, execute in this order:

### 1. Create Contracts (FIRST!)
```
Task: Create app/agents/contracts.py with all dataclass definitions
Files: Create app/agents/contracts.py
Verify: Can import from all agent files
```

### 2. Fix File Writing (CRITICAL)
```
Task: Update Shubham and Aanya to write files to disk
Files: Modify app/agents/shubham.py, app/agents/aanya.py
Verify: Files appear in /workspace/projects/{id}/
```

### 3. Fix Tilotma Handoff
```
Task: Update Tilotma to automatically start project
Files: Modify app/agents/tilotma.py
Verify: Project starts after requirements complete
```

### 4. Fix Arjun Pipeline
```
Task: Refactor Arjun to use contracts and call all agents
Files: Modify app/agents/arjun.py
Verify: All agents are called in sequence
```

### 5. Add Progress Updates
```
Task: Add ProgressMixin and update all agents
Files: Modify app/agents/mixins.py and all agent files
Verify: WebSocket receives progress updates
```

### 6. Add Tests
```
Task: Create integration tests
Files: Create tests/integration/test_agent_collaboration.py
Verify: All tests pass
```

---

## ⚠️ CRITICAL SUCCESS CRITERIA

The fix is successful when:

1. ✅ User sends message to Tilotma
2. ✅ Tilotma automatically starts project (calls Arjun)
3. ✅ Arjun successfully calls: Saanvi → Shubham → Aanya → QA → Pranav
4. ✅ Files are physically written to `/workspace/projects/{id}/`
5. ✅ Backend and frontend are deployed to GCP Cloud Run
6. ✅ User receives working URLs: `https://backend-xyz.run.app` and `https://frontend-xyz.run.app`
7. ✅ URLs are actually accessible (return 200 status)
8. ✅ Progress updates appear in real-time via WebSocket

---

## 📊 EXPECTED RESULTS

After fixes:

```
User: "Build me a task manager app"
  ↓ [2 minutes]
Tilotma: "✅ Project started! ID: proj-abc123"
  ↓ [5 minutes]
WebSocket: "Saanvi is analyzing requirements..."
  ↓ [10 minutes]
WebSocket: "Shubham is generating backend code..."
  ↓ [10 minutes]
WebSocket: "Aanya is generating frontend code..."
  ↓ [5 minutes]
WebSocket: "QA agents are testing code..."
  ↓ [10 minutes]
WebSocket: "Pranav is deploying to GCP..."
  ↓ [Done!]
Email to User:
"✅ Your task manager is ready!
- Backend: https://backend-abc123.run.app
- Frontend: https://taskmanager-abc123.run.app
- Admin: admin@example.com / password123"
```

**Total Time: ~40 minutes (fully automated)**

---

## 🔍 DEBUGGING TIPS

If issues occur:

1. **Check logs:** `logs/nexsidi.log`
2. **Check handoff state:** `context_engine.get("project:{id}:handoff")`
3. **Check files:** `ls /workspace/projects/{id}/`
4. **Check deployment:** `gcloud run services list`
5. **Test locally:** Run `pytest tests/integration/`

---

## 📝 NOTES FOR ANTIGRAVITY AGENT

- Use "Plan mode" for this task (generate plan first)
- Ask for approval before making breaking changes
- Run tests after each major change
- Commit to git after each successful fix
- If you encounter errors, explain clearly and ask for guidance

---

**END OF PLAN**

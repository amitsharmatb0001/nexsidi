# NexSidi Product Specification

## What It Does
AI platform that builds web apps. User describes app → System delivers deployed application (30-60 min).

## 12 AI Agents
1. Tilotma - User chat, requirements
2. Arjun - Orchestrates agents
3. Saanvi - Cost estimation
4. Vikram - Architecture design
5. Vanya - Design system + mockup
6. Shubham - Backend code
7. Aanya - Frontend code
8. Navya - Security testing
9. Karan - Functional testing
10. Deepika - UX testing
11. Aarav - Integration testing
12. Pranav - Deployment

## Key Features
- Live code streaming
- Real-time file creation feed
- Agent conversation logs
- Token usage metrics

## Stack
- Backend: FastAPI + PostgreSQL + Redis
- AI: Gemini + Claude
- Deploy: Google Cloud Run

## Tests

### 1. Vanya Agent
Generates design system with colors, typography, HTML mockup.

```python
@pytest.mark.asyncio
async def test_vanya_generates_design():
    from app.agents.vanya import Vanya
    vanya = Vanya("test-project")
    
    blueprint = {
        "project_name": "Test App",
        "project_type": "blog"
    }
    
    result = await vanya.execute(blueprint)
    
    assert "design_system" in result
    assert "colors" in result["design_system"]
    assert "mockup_html" in result
```

### 2. Approval Service
Creates checkpoints for user approval.

```python
@pytest.mark.asyncio
async def test_approval_checkpoint():
    from app.services.approval_service import ApprovalService
    service = ApprovalService("test-project")
    
    checkpoint = await service.create_design_preview_checkpoint(
        blueprint={},
        vanya_design={"design_system": {}, "mockup_html": ""}
    )
    
    assert "checkpoint_id" in checkpoint
    assert checkpoint["status"] == "pending"
```

### 3. File Tree API
Returns project file structure.

```python
async def test_file_tree_api():
    response = await client.get("/api/projects/test/files")
    assert response.status_code == 200
    assert "tree" in response.json()
```

### 4. Code Viewer API
Reads file content.

```python
async def test_code_viewer():
    response = await client.get("/api/projects/test/code/main.py")
    assert response.status_code == 200
    assert "content" in response.json()
```

### 5. Document Generator
Generates SDD PDF.

```python
def test_sdd_pdf():
    from app.services.document_generator import DocumentGenerator
    path = DocumentGenerator.generate_sdd_pdf(blueprint, "/tmp/sdd.pdf")
    assert os.path.exists(path)
```

### 6. OTP Service
Generates and verifies OTP.

```python
def test_otp():
    from app.services.otp_service import OTPService
    otp = OTPService.generate_otp("project-123")
    assert len(otp) == 6
    assert OTPService.verify_otp("project-123", otp) == True
```

## Success Criteria
All tests pass.

```bash
pytest tests/ -v
# Expected: All passed
```

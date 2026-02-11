from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from app.database import get_db
from app.models import User, Project
from app.schemas import ProjectCreate, ProjectResponse
from app.dependencies import get_current_user
from app.services.queue_manager import QueueManager
from app.agents.arjun import Arjun
from app.services.workspace_manager import workspace_manager
from typing import Optional, Dict, Any, List
from fastapi.responses import FileResponse, StreamingResponse
import asyncio
import json

router = APIRouter()


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create new project
    
    When does this happen?
    - User has chatted with Tilotma
    - User decides to proceed with building something
    - We create formal project entry
    
    Initial state:
    - status: 'requirements_gathering'
    - current_agent: 'saanvi' (requirements analysis)
    - No price quoted yet
    """
    try:
        new_project = Project(
            user_id=current_user.id,
            title=project_data.title,
            description=project_data.description,
            status="queued",  # Start in queued status
            current_agent="saanvi"
        )
        
        db.add(new_project)
        db.commit()
        db.refresh(new_project)
        
        # AUTO-ENQUEUE: Trigger building immediately
        try:
            queue = QueueManager()
            await queue.enqueue_project(
                project_id=str(new_project.id),
                user_id=str(current_user.id)
            )
        except Exception as qe:
            import logging
            logging.getLogger("projects").warning(f"[WARN] Auto-enqueue failed: {str(qe)}")
        
        return new_project
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("projects").error(f"Error creating project: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create project"
        )


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all projects for current user
    
    Why filter by user_id?
    - Security: Users should only see their own projects
    - Multi-tenancy: Each user has isolated data
    
    Returns empty list if user has no projects yet
    """
    try:
        projects = db.query(Project).filter(Project.user_id == current_user.id).all()
        return projects
    except Exception as e:
        import logging
        logging.getLogger("projects").error(f"Error listing projects: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve projects"
        )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get specific project details
    
    Security check:
    - Verify project exists
    - Verify project belongs to current user
    - Return 404 if not found or unauthorized (don't reveal if it exists)
    """
    try:
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.user_id == current_user.id  # Important: prevent accessing others' projects
        ).first()
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        return project
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("projects").error(f"Error getting project {project_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project details"
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete project (and all related data due to CASCADE)
    
    What gets deleted?
    - Project row
    - All conversations (CASCADE)
    - All agent_tasks (CASCADE)
    - All deployments (CASCADE)
    - All code_files (CASCADE)
    
    Why 204 No Content?
    - Successful deletion returns nothing
    - Standard REST practice
    """
    try:
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.user_id == current_user.id
        ).first()
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        db.delete(project)
        db.commit()
        
        return None  # FastAPI converts this to 204
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("projects").error(f"Error deleting project {project_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete project"
        )


@router.get("/{project_id}/status")
async def get_project_status(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get project processing status
    
    Returns queue position and estimated wait time for queued projects.
    For processing/completed projects, returns current status.
    
    Security:
    - Only project owner can check status
    - Returns 404 if project not found or unauthorized
    """
    try:
        # Verify project belongs to user
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.user_id == current_user.id
        ).first()
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Get queue status if queued/processing
        queue = QueueManager()
        queue_status = await queue.get_project_status(str(project_id)) or {}
        
        return {
            "project_id": str(project_id),
            "status": project.status,
            "current_agent": project.current_agent,
            "queue_position": queue_status.get("queue_position"),
            "estimated_wait": queue_status.get("estimated_wait"),
            "error": queue_status.get("error")  # Added: Reveal error for debugging
        }
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("projects").error(f"Error getting status for project {project_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project status"
        )


@router.get("/{project_id}/download")
async def download_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Download COMPLETE project package.
    
    Includes:
    - Source code
    - Tests
    - Screenshots
    - Software Design Document (SDD)
    - Decision history
    """
    # 1. Verify project ownership
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    try:
        workspace = workspace_manager.get_workspace(str(project_id))
        
        # Create organized package
        package_dir = tempfile.mkdtemp()
        
        # Create docs directory
        os.makedirs(os.path.join(package_dir, 'docs'), exist_ok=True)
        
        # Copy source code
        if os.path.exists(workspace['code_dir']):
            shutil.copytree(workspace['code_dir'], os.path.join(package_dir, 'source'))
        
        # Copy tests
        if os.path.exists(workspace['test_dir']):
            shutil.copytree(workspace['test_dir'], os.path.join(package_dir, 'tests'))
        
        # Copy screenshots
        if os.path.exists(workspace['screenshot_dir']):
            shutil.copytree(workspace['screenshot_dir'], os.path.join(package_dir, 'screenshots'))
        
        # Generate documentation
        sdd = await document_generator.generate_sdd(str(project_id))
        with open(os.path.join(package_dir, 'docs', 'SDD.md'), 'w', encoding='utf-8') as f:
            f.write(sdd)
        
        # Generate decision history
        decisions = decision_ledger.get_support_summary(str(project_id))
        with open(os.path.join(package_dir, 'docs', 'DECISIONS.md'), 'w', encoding='utf-8') as f:
            f.write(decisions)
        
        # Generate README (Simplified for now, or use _generate_project_readme if it existed)
        readme = f"# {project.title}\n\n{project.description}\n\nGenerated by NexSidi."
        with open(os.path.join(package_dir, 'README.md'), 'w', encoding='utf-8') as f:
            f.write(readme)
        
        # ZIP everything
        temp_dir = "/tmp" if os.name != "nt" else os.environ.get("TEMP", "C:/Windows/Temp")
        zip_base_name = os.path.join(temp_dir, f"nexsidi_{project_id}")
        zip_path = shutil.make_archive(zip_base_name, 'zip', package_dir)
        
        # CLEANUP temp package dir after zipping (optional but good practice)
        # shutil.rmtree(package_dir)
        
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{project.title.replace(' ', '_')}_complete.zip"
        )
        
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project source code not found. Has it been generated yet?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create download: {str(e)}")
@router.get("/{project_id}/code/stream")
async def stream_code_generation(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Task 2.2: Stream code generation in real-time via SSE"""
    async def event_generator():
        workspace = workspace_manager.get_workspace(str(project_id))
        workspace_path = workspace['code_dir']
        seen_files = set()
        
        while True:
            if os.path.exists(workspace_path):
                for root, dirs, files in os.walk(workspace_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        relative_path = os.path.relpath(full_path, workspace_path)
                        
                        if relative_path not in seen_files:
                            seen_files.add(relative_path)
                            try:
                                with open(full_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                yield f"data: {json.dumps({'type': 'file_created', 'path': relative_path, 'content': content[:1000], 'size': len(content)})}\n\n"
                            except: pass
            await asyncio.sleep(2)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{project_id}/files")
async def get_file_tree(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Task 2.3: Get project file tree structure"""
    workspace = workspace_manager.get_workspace(str(project_id))
    workspace_path = workspace['code_dir']
    
    if not os.path.exists(workspace_path):
        raise HTTPException(404, "Project workspace not found")
    
    def build_tree(path: str, relative_to: str) -> List[Dict]:
        items = []
        try:
            for item in os.listdir(path):
                if item in ['.git', 'venv', '__pycache__']: continue
                item_path = os.path.join(path, item)
                rel_path = os.path.relpath(item_path, relative_to)
                if os.path.isdir(item_path):
                    items.append({"name": item, "type": "folder", "path": rel_path, "children": build_tree(item_path, relative_to)})
                else:
                    items.append({"name": item, "type": "file", "path": rel_path, "size": os.path.getsize(item_path)})
        except: pass
        return items
    
    return {"project_id": str(project_id), "tree": build_tree(workspace_path, workspace_path)}


@router.get("/{project_id}/code/{filepath:path}")
async def get_file_content(
    project_id: UUID,
    filepath: str,
    current_user: User = Depends(get_current_user)
):
    """Task 2.4: Read any generated file"""
    workspace = workspace_manager.get_workspace(str(project_id))
    workspace_path = workspace['code_dir']
    full_path = os.path.abspath(os.path.join(workspace_path, filepath))
    
    if not full_path.startswith(os.path.abspath(workspace_path)):
        raise HTTPException(403, "Access denied")
    
    if not os.path.exists(full_path):
        raise HTTPException(404, "File not found")
    
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"path": filepath, "content": content, "size": len(content), "lines": len(content.split('\n'))}
    except:
        raise HTTPException(400, "Cannot read file (might be binary)")


@router.get("/{project_id}/agent-logs")
async def get_agent_logs(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Task 2.5: Get agent conversation and activity logs"""
    from app.services.context_engine import context_engine
    logs = context_engine.get_context(str(project_id), "agent_logs") or []
    return {"project_id": str(project_id), "logs": logs}


@router.post("/{project_id}/pause")
async def pause_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Task 4.3: Pause project execution"""
    from app.services.queue_manager import QueueManager
    queue = QueueManager()
    arjun = queue.get_active_arjun(str(project_id))
    
    if arjun:
        await arjun.pause_pipeline()
        return {"status": "paused"}
    
    raise HTTPException(404, "Project not actively running")


@router.post("/{project_id}/resume")
async def resume_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Task 4.3: Resume paused project"""
    from app.services.queue_manager import QueueManager
    queue = QueueManager()
    arjun = queue.get_active_arjun(str(project_id))
    
    if arjun:
        await arjun.resume_pipeline()
        return {"status": "resumed"}
    
    raise HTTPException(404, "Project not actively running")


@router.post("/{project_id}/cancel")
async def cancel_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Task 4.3: Cancel project execution"""
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(404, "Project not found")
        
    project.status = "cancelled"
    db.commit()
    
    from app.services.queue_manager import QueueManager
    queue = QueueManager()
    # Note: queue.cancel_project should be implemented in QueueManager if it doesn't exist
    await queue.cancel_project(str(project_id))
    
    return {"status": "cancelled"}

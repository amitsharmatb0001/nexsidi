"""
Deployment API Endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.database import get_db
from app.models import User, Project, Deployment
from app.dependencies import get_current_user
from app.services.gcp_service import GCPService
from app.services.workspace_manager import workspace_manager
from datetime import datetime
import os

router = APIRouter()


@router.post("/{project_id}/deploy")
async def deploy_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Deploy project to GCP Cloud Run.
    
    This endpoint:
    1. Verifies project ownership
    2. Checks if code has been generated
    3. Builds Docker container
    4. Deploys to Cloud Run
    5. Returns deployment status
    """
    # Verify project exists and user owns it
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Check if project has generated code
    workspace = workspace_manager.get_workspace(str(project_id))
    if not os.path.exists(workspace['code_dir']):
        raise HTTPException(
            status_code=400,
            detail="Project code not generated yet. Please wait for agents to complete building."
        )
    
    try:
        # Initialize GCP service
        gcp_service = GCPService()
        if not gcp_service.authenticate():
            raise HTTPException(
                status_code=500,
                detail="Failed to authenticate with GCP. Check service account configuration."
            )
        
        # Generate service name (lowercase, alphanumeric only)
        service_name = f"nexsidi-{str(project_id)[:8]}".lower()
        
        # Create deployment record
        deployment = Deployment(
            project_id=project_id,
            deployment_type="gcp_cloud_run",
            status="deploying",
            config={
                "service_name": service_name,
                "region": gcp_service.region
            },
            logs="Deployment initiated..."
        )
        db.add(deployment)
        db.commit()
        db.refresh(deployment)
        
        # Build and push Docker image
        image_tag = gcp_service.build_and_push_image(
            workspace_path=workspace['root'],
            image_name=service_name
        )
        
        if not image_tag:
            deployment.status = "failed"
            deployment.logs = "Failed to build/push Docker image"
            db.commit()
            raise HTTPException(status_code=500, detail="Failed to build Docker image")
        
        # Deploy to Cloud Run
        deployment_result = gcp_service.deploy_to_cloud_run(
            service_name=service_name,
            image_url=image_tag,
            port=3000  # Assuming Vite default port
        )
        
        if not deployment_result:
            deployment.status = "failed"
            deployment.logs = "Failed to deploy to Cloud Run"
            db.commit()
            raise HTTPException(status_code=500, detail="Failed to deploy to Cloud Run")
        
        # Update deployment record
        deployment.status = "completed"
        deployment.deployment_url = deployment_result['url']
        deployment.deployed_at = datetime.utcnow()
        deployment.logs = f"Successfully deployed to {deployment_result['url']}"
        db.commit()
        
        return {
            "deployment_id": str(deployment.id),
            "status": "completed",
            "url": deployment_result['url'],
            "service_name": service_name,
            "message": "Deployment successful!"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # Update deployment status to failed
        if 'deployment' in locals():
            deployment.status = "failed"
            deployment.logs = f"Deployment error: {str(e)}"
            db.commit()
        
        raise HTTPException(
            status_code=500,
            detail=f"Deployment failed: {str(e)}"
        )


@router.get("/{project_id}/deployments")
async def list_deployments(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all deployments for a project"""
    # Verify project ownership
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    deployments = db.query(Deployment).filter(
        Deployment.project_id == project_id
    ).order_by(Deployment.created_at.desc()).all()
    
    return {
        "project_id": str(project_id),
        "deployments": [
            {
                "deployment_id": str(d.id),
                "status": d.status,
                "url": d.deployment_url,
                "deployed_at": d.deployed_at,
                "created_at": d.created_at
            }
            for d in deployments
        ]
    }


@router.get("/{project_id}/deployments/latest")
async def get_latest_deployment(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the most recent deployment status"""
    # Verify project ownership
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    deployment = db.query(Deployment).filter(
        Deployment.project_id == project_id
    ).order_by(Deployment.created_at.desc()).first()
    
    if not deployment:
        raise HTTPException(status_code=404, detail="No deployments found for this project")
    
    return {
        "deployment_id": str(deployment.id),
        "status": deployment.status,
        "url": deployment.deployment_url,
        "deployed_at": deployment.deployed_at,
        "logs": deployment.logs,
        "config": deployment.config
    }


@router.get("/{project_id}/deployments/{deployment_id}")
async def get_deployment_details(
    project_id: UUID,
    deployment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get specific deployment details"""
    # Verify project ownership
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    deployment = db.query(Deployment).filter(
        Deployment.id == deployment_id,
        Deployment.project_id == project_id
    ).first()
    
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return {
        "deployment_id": str(deployment.id),
        "project_id": str(deployment.project_id),
        "status": deployment.status,
        "deployment_type": deployment.deployment_type,
        "url": deployment.deployment_url,
        "deployed_at": deployment.deployed_at,
        "created_at": deployment.created_at,
        "logs": deployment.logs,
        "config": deployment.config
    }

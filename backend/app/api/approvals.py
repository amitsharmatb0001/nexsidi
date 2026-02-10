from fastapi import APIRouter, Depends, HTTPException, Body
from app.services.approval_service import ApprovalService
from app.dependencies import get_current_user
from app.models import User
from typing import Optional

router = APIRouter()

@router.post("/{project_id}/approve/{checkpoint_id}")
async def approve_checkpoint(
    project_id: str,
    checkpoint_id: str,
    current_user: User = Depends(get_current_user)
):
    """User clicked [Approve] button"""
    approval_service = ApprovalService(project_id)
    await approval_service.update_checkpoint_status(checkpoint_id, "approved")
    return {"status": "approved", "checkpoint_id": checkpoint_id}

@router.post("/{project_id}/reject/{checkpoint_id}")
async def reject_checkpoint(
    project_id: str,
    checkpoint_id: str,
    feedback: Optional[str] = Body(None, embed=True),
    current_user: User = Depends(get_current_user)
):
    """User clicked [Reject] button"""
    approval_service = ApprovalService(project_id)
    await approval_service.update_checkpoint_status(checkpoint_id, "rejected", feedback)
    return {"status": "rejected", "checkpoint_id": checkpoint_id, "feedback": feedback}

@router.post("/{project_id}/request-changes/{checkpoint_id}")
async def request_changes(
    project_id: str,
    checkpoint_id: str,
    feedback: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user)
):
    """User clicked [Request Changes] button"""
    approval_service = ApprovalService(project_id)
    await approval_service.update_checkpoint_status(checkpoint_id, "changes_requested", feedback)
    return {"status": "changes_requested", "checkpoint_id": checkpoint_id, "feedback": feedback}

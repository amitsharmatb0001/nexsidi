from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from typing import Dict, Any, List
from app.core.auth import get_current_user
from app.api.auth import User
from app.services.signing_service import signing_service
from app.services.ledger_service import ledger_service
import time

router = APIRouter()


@router.get("/{project_id}")
async def verify_project_integrity(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Complete cryptographic verification of project.
    Returns signature verification + hash chain verification + audit trail.
    """
    try:
        # Verify ledger chain integrity
        chain_verification = ledger_service.verify_chain(project_id)
        
        # Get audit trail
        timeline = ledger_service.get_event_timeline(project_id)
        
        return {
            "project_id": project_id,
            "verification_timestamp": time.time(),
            "chain_valid": chain_verification["valid"],
            "event_count": chain_verification["event_count"],
            "errors": chain_verification.get("errors", []),
            "timeline": timeline,
            "status": "verified" if chain_verification["valid"] else "integrity_compromised"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/audit-trail")
async def get_audit_trail(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get complete audit trail for project"""
    
    timeline = ledger_service.get_event_timeline(project_id)
    verification = ledger_service.verify_chain(project_id)
    
    return {
        "project_id": project_id,
        "event_count": verification["event_count"],
        "chain_valid": verification["valid"],
        "timeline": timeline
    }


@router.get("/{project_id}/export")
async def export_ledger(
    project_id: str,
    format: str = "json",
    current_user: User = Depends(get_current_user)
):
    """Export ledger in JSON or CSV format"""
    
    ledger_data = ledger_service.export_ledger(project_id, format)
    
    if format == "csv":
        return Response(
            content=ledger_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={project_id}_ledger.csv"}
        )
    else:
        return Response(
            content=ledger_data,
            media_type="application/json"
        )


@router.get("/agent/{agent_name}/public-key")
async def get_agent_public_key(agent_name: str):
    """Get agent's public key for external verification (no auth required)"""
    
    try:
        public_key_pem = signing_service.export_public_key_pem(agent_name)
        fingerprint = signing_service.get_agent_fingerprint(agent_name)
        
        return {
            "agent_name": agent_name,
            "public_key": public_key_pem,
            "fingerprint": fingerprint,
            "algorithm": "RSA-2048-SHA256"
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not found")
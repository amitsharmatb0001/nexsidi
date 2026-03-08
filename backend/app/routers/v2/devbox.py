"""DevBox REST API — v2.

Endpoints for managing cloud DevBox execution environments.
Requires authentication (JWT). DevBox access is gated by billing plan.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/devbox", tags=["devbox"])


def _get_current_user_id() -> str:
    """Placeholder dependency — replaced by real auth in main.py.

    In production, this is overridden with the JWT-based auth dependency
    that extracts user_id from the access token.
    """
    raise HTTPException(status_code=401, detail="Authentication required")


@router.get("/status")
async def get_devbox_status(
    user_id: str = Depends(_get_current_user_id),
) -> dict[str, Any]:
    """Get the current user's DevBox status.

    Returns DevBox details if one exists, or null if none provisioned.
    """
    from app.services.devbox_manager import get_devbox_manager

    manager = get_devbox_manager()
    record = await manager.get_user_devbox(user_id)

    if not record:
        return {"devbox": None, "message": "No active DevBox"}

    return {
        "devbox": {
            "id": record.id,
            "status": record.status,
            "plan": record.plan,
            "service_url": record.service_url,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "namespace": record.namespace,
        },
    }


@router.post("/extend")
async def extend_devbox(
    hours: int = 24,
    user_id: str = Depends(_get_current_user_id),
) -> dict[str, Any]:
    """Extend the current user's DevBox TTL.

    Args:
        hours: Number of hours to extend (default 24).
    """
    from app.services.devbox_manager import get_devbox_manager

    manager = get_devbox_manager()
    record = await manager.get_user_devbox(user_id)

    if not record:
        raise HTTPException(status_code=404, detail="No active DevBox found")

    success = await manager.extend_devbox(record.id, hours)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to extend DevBox")

    # Refresh state
    updated = await manager.get_devbox(record.id)
    return {
        "extended": True,
        "new_expires_at": updated.expires_at if updated else record.expires_at,
    }


@router.delete("")
async def delete_devbox(
    user_id: str = Depends(_get_current_user_id),
) -> dict[str, Any]:
    """Terminate and clean up the current user's DevBox."""
    from app.services.devbox_manager import get_devbox_manager

    manager = get_devbox_manager()
    record = await manager.get_user_devbox(user_id)

    if not record:
        raise HTTPException(status_code=404, detail="No active DevBox found")

    success = await manager.deprovision_devbox(record.id)
    return {"terminated": success, "devbox_id": record.id}

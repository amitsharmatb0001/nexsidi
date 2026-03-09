"""Steering REST API — v2.

Endpoints for mid-flight user steering during pipeline generation.
Users can halt, pivot, or send feedback to Tilotma via these endpoints.

Phase 5B: Full implementation with SteeringService integration.
CRITICAL-4 FIX: Replaced placeholder auth with real CurrentContext dependency.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import CurrentContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/steering", tags=["steering"])


class SteerRequest(BaseModel):
    """Request body for steering a pipeline run."""

    message: str = Field(..., min_length=1, max_length=2000)
    action: str = Field(
        default="feedback",
        pattern="^(halt|pivot|feedback)$",
        description="Steering action: halt, pivot, or feedback",
    )


class SteerResponse(BaseModel):
    """Response from the steering service."""

    acknowledged: bool = True
    action: str
    tilotma_response: str | None = None
    pipeline_status: str | None = None


@router.post("/pipeline/{run_id}/steer", response_model=SteerResponse)
async def steer_pipeline(
    run_id: str,
    request: SteerRequest,
    ctx: CurrentContext,
) -> SteerResponse:
    """Send a steering command to a running pipeline.

    Actions:
    - **halt**: Pause the pipeline immediately
    - **pivot**: Change direction (requires message with new direction)
    - **feedback**: Send feedback to Tilotma for consideration
    """
    try:
        from app.services.steering_service import get_steering_service

        service = get_steering_service()
        result = await service.send_user_message(
            run_id=run_id,
            user_id=ctx.user_id,
            message=request.message,
            action=request.action,
        )

        return SteerResponse(
            acknowledged=True,
            action=request.action,
            tilotma_response=result.get("tilotma_response"),
            pipeline_status=result.get("pipeline_status"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("steering_failed", run_id=run_id, error=str(exc)[:200])
        raise HTTPException(status_code=500, detail="Steering failed")


@router.get("/pipeline/{run_id}/steering-history")
async def get_steering_history(
    run_id: str,
    ctx: CurrentContext,
) -> dict[str, Any]:
    """Get the steering message history for a pipeline run."""
    try:
        from app.services.steering_service import get_steering_service

        service = get_steering_service()
        history = await service.get_history(run_id)
        return {"run_id": run_id, "messages": history}
    except Exception as exc:
        logger.error("steering_history_failed", run_id=run_id, error=str(exc)[:200])
        return {"run_id": run_id, "messages": []}

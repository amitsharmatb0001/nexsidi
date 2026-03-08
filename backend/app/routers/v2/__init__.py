"""NexSidi API v2 — New directive features.

v1 (/api/v1/): Core features — pipeline, agents, checkpoints, billing, auth
v2 (/api/v2/): New directive features — DevBox, steering, SDD, visual tests

Existing v1 endpoints remain unchanged. New routers mount under v2.
"""

from __future__ import annotations

from fastapi import APIRouter

v2_router = APIRouter(prefix="/api/v2", tags=["v2"])

# Import sub-routers
from app.routers.v2.devbox import router as devbox_router
from app.routers.v2.steering import router as steering_router

v2_router.include_router(devbox_router)
v2_router.include_router(steering_router)

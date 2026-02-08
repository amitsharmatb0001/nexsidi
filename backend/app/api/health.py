"""
Health Check API
"""

from fastapi import APIRouter
from app.services.monitoring import system_monitor

router = APIRouter()


@router.get("/health/detailed")
async def detailed_health():
    """Detailed system health check"""
    
    health = await system_monitor.check_health()
    return health


@router.get("/health/system")
async def system_stats():
    """System resource statistics"""
    
    stats = system_monitor.get_system_stats()
    return stats

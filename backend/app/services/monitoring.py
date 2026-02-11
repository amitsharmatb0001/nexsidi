"""
System Monitoring - Health Checks & Alerts
"""

import psutil
import logging
from typing import Dict, Any
from app.services.queue_manager import QueueManager

logger = logging.getLogger("monitoring")


class SystemMonitor:
    """Monitor system health"""
    
    def __init__(self):
        self.queue_manager = QueueManager()
    
    async def check_health(self) -> Dict[str, Any]:
        """Check all system components"""
        
        try:
            queue_size = await self.queue_manager.get_queue_size()
            active_workers = await self._get_active_workers()
            redis_status = "connected"
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            queue_size = 0
            active_workers = 0
            redis_status = "unavailable"
            
        health = {
            'timestamp': __import__('time').time(),
            'queue_size': queue_size,
            'active_workers': active_workers,
            'redis_status': redis_status,
            'memory_usage': psutil.virtual_memory().percent,
            'cpu_usage': psutil.cpu_percent(interval=1),
            'disk_usage': psutil.disk_usage('/').percent,
        }
        
        # Generate alerts
        alerts = []
        
        if health['queue_size'] >= 8:
            alerts.append("[WARN] Queue nearly full (8/10)")
        if health['memory_usage'] >= 80:
            alerts.append("[WARN] Memory usage critical (>80%)")
        if health['cpu_usage'] >= 90:
            alerts.append("[WARN] CPU usage critical (>90%)")
        if health['disk_usage'] >= 85:
            alerts.append("[WARN] Disk usage critical (>85%)")
        
        health['alerts'] = alerts
        health['status'] = 'healthy' if not alerts else 'warning'
        
        for alert in alerts:
            logger.warning(alert)
        
        return health
    
    async def _get_active_workers(self) -> int:
        """Count active processing projects"""
        return self.queue_manager.redis.scard('projects_processing')
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Get detailed system statistics"""
        return {
            'cpu': {
                'percent': psutil.cpu_percent(interval=1),
                'count': psutil.cpu_count()
            },
            'memory': {
                'percent': psutil.virtual_memory().percent,
                'total_gb': psutil.virtual_memory().total / (1024**3),
                'available_gb': psutil.virtual_memory().available / (1024**3)
            },
            'disk': {
                'percent': psutil.disk_usage('/').percent,
                'total_gb': psutil.disk_usage('/').total / (1024**3),
                'free_gb': psutil.disk_usage('/').free / (1024**3)
            }
        }


# Global instance
system_monitor = SystemMonitor()

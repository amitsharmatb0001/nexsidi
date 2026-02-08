"""
CELERY APPLICATION CONFIGURATION
================================
Location: app/core/celery_app.py

Centralized Celery instance for asynchronous background tasks.
"""

from celery import Celery
from app.core.config import settings

# Initialize Celery
# =================
# Broker: Where tasks are stored (Redis DB 0)
# Backend: Where results are stored (Redis DB 1)
celery_app = Celery(
    'nexsidi',
    broker=settings.redis_url.replace("/0", "/0"), # Default queue
    backend=settings.redis_url.replace("/0", "/1") # Results
)

# Configuration settings
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Kolkata',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=900,  # 15 minutes max per task
    worker_prefetch_multiplier=1,  # Process one task at a time
    worker_max_tasks_per_child=10,  # Restart worker after 10 tasks (prevent leaks)
)

# Auto-discover tasks from these modules
celery_app.autodiscover_tasks(['app.services.queue_manager'])

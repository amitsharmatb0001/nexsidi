"""Celery task definitions for background pipeline execution.

When ``use_celery=True``, pipeline runs are dispatched to Celery workers
instead of being executed in-process via ``asyncio.create_task()``.

This enables horizontal scaling: multiple workers can process pipelines
concurrently across different machines, using Valkey/Redis as the broker.
"""

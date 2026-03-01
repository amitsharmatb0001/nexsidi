"""Celery distributed task queue configuration for service integration."""

from app.agents.service_configs import ServiceConfig, register_service

CELERY = ServiceConfig(
    name="celery",
    display_name="Celery",
    category="task_queue",
    supported_languages=("python",),
    package_dependencies={
        "python": ("celery", "redis", "flower"),
    },
    environment_variables=(
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "CELERY_TASK_ALWAYS_EAGER",
    ),
    rules=(
        "ALWAYS use Redis as the Celery broker for simplicity and performance; "
        "avoid RabbitMQ unless the project explicitly requires advanced AMQP "
        "routing features like topic exchanges or priority queues.",

        "ALWAYS set task_acks_late=True in the Celery config so that tasks are "
        "acknowledged only after successful execution, preventing message loss "
        "if a worker crashes mid-task.",

        "NEVER call time.sleep() inside a Celery task; use countdown= or eta= "
        "parameters on apply_async() to schedule delayed execution without "
        "blocking the worker process.",

        "ALWAYS set both soft_time_limit and time_limit on every task decorator "
        "to prevent runaway tasks from consuming worker resources indefinitely; "
        "handle SoftTimeLimitExceeded to perform graceful cleanup.",

        "ALWAYS set task_reject_on_worker_lost=True so that tasks running on a "
        "worker that exits unexpectedly are re-queued instead of silently lost.",

        "ALWAYS design tasks to be idempotent (safe to retry) by using database "
        "upserts, checking completion flags, or leveraging idempotency keys "
        "before performing side effects.",

        "ALWAYS use Celery Beat for periodic/scheduled tasks instead of external "
        "cron jobs; define the beat_schedule in the Celery config so the schedule "
        "is version-controlled alongside application code.",

        "NEVER pass large objects (files, query results, ORM instances) as task "
        "arguments; pass only IDs or keys and fetch the data from the database "
        "or object store inside the task to keep the broker message size small.",

        "ALWAYS use task routing with dedicated queues for different priority "
        "levels (e.g. 'high', 'default', 'low') and configure workers to "
        "consume from specific queues with -Q flag.",

        "ALWAYS enable Flower monitoring dashboard in non-production environments "
        "and set up Prometheus/StatsD export for production task observability.",

        "ALWAYS configure max_retries with exponential retry_backoff=True and "
        "retry_backoff_max on tasks that call external services to handle "
        "transient failures without overwhelming downstream systems.",

        "ALWAYS set task_serializer='json' and accept_content=['json'] to avoid "
        "pickle deserialization vulnerabilities; NEVER use pickle serialization "
        "in production environments.",
    ),
    golden_examples={
        "celery_app": (
            "import os\n"
            "from celery import Celery\n"
            "\n"
            "app = Celery(\"worker\")\n"
            "\n"
            "app.conf.update(\n"
            "    broker_url=os.environ[\"CELERY_BROKER_URL\"],\n"
            "    result_backend=os.environ[\"CELERY_RESULT_BACKEND\"],\n"
            "    task_serializer=\"json\",\n"
            "    accept_content=[\"json\"],\n"
            "    result_serializer=\"json\",\n"
            "    timezone=\"UTC\",\n"
            "    enable_utc=True,\n"
            "    task_acks_late=True,\n"
            "    task_reject_on_worker_lost=True,\n"
            "    worker_prefetch_multiplier=1,\n"
            "    task_track_started=True,\n"
            "    task_default_queue=\"default\",\n"
            "    task_routes={\n"
            "        \"app.tasks.critical.*\": {\"queue\": \"high\"},\n"
            "        \"app.tasks.reports.*\": {\"queue\": \"low\"},\n"
            "    },\n"
            ")\n"
            "\n"
            "app.autodiscover_tasks([\"app.tasks\"])"
        ),
        "task": (
            "from celery import shared_task\n"
            "from celery.exceptions import SoftTimeLimitExceeded\n"
            "\n"
            "\n"
            "@shared_task(\n"
            "    bind=True,\n"
            "    max_retries=5,\n"
            "    retry_backoff=True,\n"
            "    retry_backoff_max=600,\n"
            "    soft_time_limit=120,\n"
            "    time_limit=180,\n"
            "    acks_late=True,\n"
            ")\n"
            "def send_notification(self, user_id: int, message: str) -> dict:\n"
            "    \"\"\"Send a push notification to a user.\"\"\"\n"
            "    try:\n"
            "        user = User.objects.get(id=user_id)\n"
            "        result = notification_service.send(user.device_token, message)\n"
            "        return {\"status\": \"sent\", \"user_id\": user_id}\n"
            "    except SoftTimeLimitExceeded:\n"
            "        logger.warning(\"Task timed out\", user_id=user_id)\n"
            "        return {\"status\": \"timeout\", \"user_id\": user_id}\n"
            "    except ExternalServiceError as exc:\n"
            "        raise self.retry(exc=exc)\n"
            "    except User.DoesNotExist:\n"
            "        logger.error(\"User not found\", user_id=user_id)\n"
            "        return {\"status\": \"skipped\", \"user_id\": user_id}"
        ),
        "periodic_task": (
            "from celery.schedules import crontab\n"
            "\n"
            "app.conf.beat_schedule = {\n"
            "    \"cleanup-expired-sessions\": {\n"
            "        \"task\": \"app.tasks.maintenance.cleanup_sessions\",\n"
            "        \"schedule\": crontab(minute=0, hour=\"*/6\"),\n"
            "        \"kwargs\": {\"max_age_hours\": 72},\n"
            "    },\n"
            "    \"send-daily-digest\": {\n"
            "        \"task\": \"app.tasks.notifications.send_digest\",\n"
            "        \"schedule\": crontab(minute=0, hour=8),\n"
            "        \"options\": {\"queue\": \"low\"},\n"
            "    },\n"
            "    \"sync-inventory\": {\n"
            "        \"task\": \"app.tasks.sync.inventory_sync\",\n"
            "        \"schedule\": 300.0,  # every 5 minutes\n"
            "        \"options\": {\"queue\": \"high\", \"expires\": 240},\n"
            "    },\n"
            "}"
        ),
    },
    setup_code={
        "python": (
            "import os\n"
            "from celery import Celery\n"
            "\n"
            "celery_app = Celery(\"worker\")\n"
            "celery_app.conf.broker_url = os.environ[\"CELERY_BROKER_URL\"]\n"
            "celery_app.conf.result_backend = os.environ[\"CELERY_RESULT_BACKEND\"]\n"
            "celery_app.conf.task_serializer = \"json\"\n"
            "celery_app.conf.accept_content = [\"json\"]\n"
            "celery_app.conf.task_acks_late = True\n"
            "celery_app.conf.task_reject_on_worker_lost = True\n"
            "celery_app.autodiscover_tasks([\"app.tasks\"])"
        ),
    },
)

register_service(CELERY)

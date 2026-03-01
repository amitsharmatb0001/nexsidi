"""Service/integration configuration registry for third-party service integration.

Each supported service has a ``ServiceConfig`` that tells Shubham how to
generate integration code for external services like task queues, payment
processors, email providers, object storage, and search engines.

Usage::

    from app.agents.service_configs import get_service_config

    config = get_service_config("celery")
    print(config.category)           # "task_queue"
    print(len(config.rules))         # 10+
    print(config.golden_examples)    # {"worker": "...", "task": "...", ...}
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ServiceConfig:
    """Immutable configuration for a third-party service integration.

    Attributes:
        name: Canonical name (e.g. "celery", "stripe", "s3").
        display_name: Human-readable name (e.g. "Celery", "Stripe").
        category: Service category ("task_queue", "payment", "email",
                  "storage", "search").
        supported_languages: Languages this config supports.
        package_dependencies: Map of language to list of package names.
        environment_variables: Required env vars for this service.
        rules: Service-specific integration rules.
        golden_examples: Map of example name to code snippet.
        setup_code: Map of language to initialization/setup code snippet.
    """

    name: str
    display_name: str
    category: str
    supported_languages: tuple[str, ...]
    package_dependencies: dict[str, tuple[str, ...]]
    environment_variables: tuple[str, ...]
    rules: tuple[str, ...]
    golden_examples: dict[str, str]
    setup_code: dict[str, str]


# ── Registry ────────────────────────────────────────────────────────

_SERVICE_REGISTRY: dict[str, ServiceConfig] = {}


def register_service(config: ServiceConfig) -> None:
    """Register a service configuration."""
    _SERVICE_REGISTRY[config.name] = config
    logger.debug("service_registered", name=config.name)


def get_service_config(name: str) -> ServiceConfig:
    """Get service config by canonical name or alias."""
    key = name.lower().replace("-", "_").replace(" ", "_")

    if key in _SERVICE_REGISTRY:
        return _SERVICE_REGISTRY[key]

    _aliases: dict[str, str] = {
        "celery_worker": "celery", "task_queue": "celery",
        "bull": "bullmq", "bull_mq": "bullmq", "bullqueue": "bullmq",
        "stripe_payments": "stripe", "payments": "stripe",
        "sendgrid_email": "sendgrid", "email": "sendgrid",
        "aws_s3": "s3", "object_storage": "s3", "minio": "s3",
        "elastic": "elasticsearch", "es": "elasticsearch", "opensearch": "elasticsearch",
    }
    resolved = _aliases.get(key, key)
    if resolved in _SERVICE_REGISTRY:
        return _SERVICE_REGISTRY[resolved]

    raise KeyError(f"No service config for '{name}'")


def list_services() -> list[str]:
    """List all registered service names."""
    return sorted(_SERVICE_REGISTRY.keys())


# ── Auto-register all service configs on import ──────────────────

from app.agents.service_configs import celery_config as _celery  # noqa: E402, F401
from app.agents.service_configs import bullmq_config as _bullmq  # noqa: E402, F401
from app.agents.service_configs import stripe_config as _stripe  # noqa: E402, F401
from app.agents.service_configs import sendgrid_config as _sendgrid  # noqa: E402, F401
from app.agents.service_configs import s3_config as _s3  # noqa: E402, F401
from app.agents.service_configs import elasticsearch_config as _es  # noqa: E402, F401

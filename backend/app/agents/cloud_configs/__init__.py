"""Cloud platform configuration registry for deployment and hosting.

Each supported cloud platform has a ``CloudConfig`` that tells the deployment
agent how to generate configuration files, set environment variables, and
execute deploy commands:
- Platform category (serverless, container, PaaS, static)
- Supported frameworks per platform
- Config file templates (vercel.json, Dockerfile, fly.toml, etc.)
- Required environment variables
- Deploy CLI commands
- Feature flags (preview deployments, custom domains, free tier)

New platforms are added by creating a config entry in ``configs.py``
and calling ``register_cloud()`` at module level.

Usage::

    from app.agents.cloud_configs import get_cloud_config

    config = get_cloud_config("vercel")
    print(config.category)          # "serverless"
    print(config.deploy_command)    # "vercel deploy --prod"
    print(config.free_tier)         # True
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CloudConfig:
    """Immutable configuration for a cloud hosting platform.

    Attributes:
        name: Canonical name (e.g. ``"vercel"``, ``"aws_ecs"``).
        display_name: Human-readable name (e.g. ``"Vercel"``, ``"AWS ECS"``).
        category: Platform category (``"serverless"``, ``"container"``,
                  ``"paas"``, ``"static"``).
        supported_frameworks: Which backend/frontend frameworks the platform
                              supports (e.g. ``("nextjs", "react", "vue")``).
        config_files: Map of file name to template content
                      (e.g. ``{"vercel.json": "..."}``) .
        environment_variables: Required environment variables for deployment.
        deploy_command: The CLI command to deploy (e.g. ``"vercel deploy --prod"``).
        supports_preview: Whether the platform supports preview/staging deployments.
        supports_custom_domain: Whether custom domain binding is supported.
        free_tier: Whether the platform offers a free tier.
        rules: Deployment best practices for this platform.
    """

    name: str
    display_name: str
    category: str
    supported_frameworks: tuple[str, ...]
    config_files: dict[str, str]
    environment_variables: tuple[str, ...]
    deploy_command: str
    supports_preview: bool
    supports_custom_domain: bool
    free_tier: bool
    rules: tuple[str, ...]


# -- Registry ----------------------------------------------------------------

_CLOUD_REGISTRY: dict[str, CloudConfig] = {}


def register_cloud(config: CloudConfig) -> None:
    """Register a cloud platform configuration."""
    _CLOUD_REGISTRY[config.name] = config
    logger.debug("cloud_registered", name=config.name)


def get_cloud_config(name: str) -> CloudConfig:
    """Get cloud config by canonical name or alias.

    Raises ``KeyError`` for unknown platforms (no silent fallback).
    """
    key = name.lower().replace("-", "_").replace(" ", "_")

    # Direct match
    if key in _CLOUD_REGISTRY:
        return _CLOUD_REGISTRY[key]

    # Alias resolution
    _aliases: dict[str, str] = {
        "aws": "aws_ecs",
        "ecs": "aws_ecs",
        "gcp": "gcp_cloud_run",
        "cloud_run": "gcp_cloud_run",
        "cloudrun": "gcp_cloud_run",
        "azure": "azure_app_service",
        "app_service": "azure_app_service",
        "do": "digitalocean",
        "digital_ocean": "digitalocean",
        "flyio": "fly_io",
        "fly": "fly_io",
    }
    resolved = _aliases.get(key, key)
    if resolved in _CLOUD_REGISTRY:
        return _CLOUD_REGISTRY[resolved]

    raise KeyError(f"No cloud config for '{name}'")


def list_clouds() -> list[str]:
    """List all registered cloud platform names."""
    return sorted(_CLOUD_REGISTRY.keys())


# -- Auto-register all cloud configs on import --------------------------------

from app.agents.cloud_configs import configs as _configs  # noqa: E402, F401

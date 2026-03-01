"""Mobile framework configuration registry for NexSidi v2.

Each supported mobile framework has a ``MobileConfig`` that tells the mobile
agent how to generate screens, navigation, state management, and components:
- Framework language and platform target
- File structure conventions
- Framework-specific coding rules
- Golden code examples for each generation step
- Build tooling and package manager details

New frameworks are added by creating a config entry in ``configs.py``
and calling ``register_mobile()`` at module level.

Usage::

    from app.agents.mobile_configs import get_mobile_config

    config = get_mobile_config("flutter")
    print(config.language)              # "dart"
    print(config.platform)              # "cross_platform"
    print(config.package_manager)       # "pub"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MobileConfig:
    """Immutable configuration for a mobile framework.

    Attributes:
        name: Canonical name (e.g. ``"react_native"``, ``"flutter"``).
        display_name: Human-readable name (e.g. ``"React Native"``, ``"Flutter"``).
        language: Primary language (e.g. ``"typescript"``, ``"dart"``).
        code_block_lang: Language tag for markdown code fences.
        platform: Target platform (``"cross_platform"``, ``"ios"``, ``"android"``).
        component_extension: File extension for components (e.g. ``".tsx"``, ``".dart"``).
        file_structure: Map of step name to expected file path pattern.
        rules: Framework-specific coding rules (minimum 5).
        golden_examples: Map of step name to example code snippet.
        supports_hot_reload: Whether the framework supports hot reload.
        min_sdk_version: Minimum SDK or OS version required.
        package_manager: Package manager name (e.g. ``"npm"``, ``"pub"``).
    """

    name: str                               # "react_native", "flutter", "swift", "kotlin_compose", "expo"
    display_name: str                       # "React Native", "Flutter", etc.
    language: str                           # "typescript", "dart", "swift", "kotlin"
    code_block_lang: str                    # For markdown code fences
    platform: str                           # "cross_platform", "ios", "android"
    component_extension: str                # ".tsx", ".dart", ".swift", ".kt"
    file_structure: dict[str, str]          # Step name -> expected file path
    rules: tuple[str, ...]                  # 10-14 framework-specific rules
    golden_examples: dict[str, str]         # Step name -> code snippet
    supports_hot_reload: bool               # Hot reload support
    min_sdk_version: str                    # Minimum SDK version
    package_manager: str                    # "npm", "pub", "cocoapods", "gradle"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name is required")
        if not self.display_name:
            raise ValueError("display_name is required")
        if self.platform not in ("cross_platform", "ios", "android"):
            raise ValueError(f"Invalid platform: {self.platform}")
        if len(self.rules) < 5:
            raise ValueError(f"At least 5 rules required, got {len(self.rules)}")


# -- Registry ----------------------------------------------------------------

_MOBILE_REGISTRY: dict[str, MobileConfig] = {}

_aliases: dict[str, str] = {
    "rn": "react_native",
    "reactnative": "react_native",
    "react-native": "react_native",
    "react_native": "react_native",
    "swiftui": "swift",
    "ios": "swift",
    "kotlin": "kotlin_compose",
    "jetpack": "kotlin_compose",
    "jetpack_compose": "kotlin_compose",
    "android": "kotlin_compose",
    "dart": "flutter",
}


def register_mobile(config: MobileConfig) -> None:
    """Register a mobile framework configuration."""
    if config.name in _MOBILE_REGISTRY:
        raise ValueError(f"Mobile config '{config.name}' already registered")
    _MOBILE_REGISTRY[config.name] = config
    logger.debug("mobile_config_registered", extra={"name": config.name})


def get_mobile_config(name: str) -> MobileConfig:
    """Get mobile config by canonical name or alias (case-insensitive).

    Raises ``KeyError`` for unknown frameworks (no silent fallback).
    """
    key = name.lower().strip().replace(" ", "_").replace(".", "").replace("-", "_")
    resolved = _aliases.get(key, key)
    if resolved not in _MOBILE_REGISTRY:
        raise KeyError(
            f"Unknown mobile framework '{name}'. "
            f"Available: {sorted(_MOBILE_REGISTRY.keys())}"
        )
    return _MOBILE_REGISTRY[resolved]


def list_mobile_frameworks() -> list[str]:
    """List all registered mobile framework names (sorted)."""
    return sorted(_MOBILE_REGISTRY.keys())


# -- Auto-register all mobile configs on import --------------------------------

from app.agents.mobile_configs import configs as _configs  # noqa: E402, F401

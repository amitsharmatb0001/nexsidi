"""Desktop framework configuration registry for NexSidi v2.

Each supported desktop framework has a ``DesktopConfig`` that tells the
desktop agent how to generate application scaffolding, configure IPC,
and package native executables:
- Main/backend process setup and security configuration
- Preload scripts and context bridge (Electron) or Tauri commands (Tauri)
- IPC patterns for frontend-backend communication
- Packaging and distribution configuration
- Platform-specific build targets (Windows, macOS, Linux)

New frameworks are added by creating a config entry in ``configs.py``
and calling ``register_desktop()`` at module level.

Usage::

    from app.agents.desktop_configs import get_desktop_config

    config = get_desktop_config("electron")
    print(config.display_name)           # "Electron"
    print(config.backend_language)       # "javascript"
    print(config.supports_auto_update)   # True
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DesktopConfig:
    """Immutable configuration for a desktop framework.

    Attributes:
        name: Canonical name (e.g. ``"electron"``, ``"tauri"``).
        display_name: Human-readable name (e.g. ``"Electron"``, ``"Tauri"``).
        language: Primary language (e.g. ``"typescript"``, ``"rust"``).
        code_block_lang: Language identifier for markdown code fences.
        backend_language: Language used in the backend/main process.
        frontend_framework: Default frontend framework
            (``"react"``, ``"vue"``, ``"svelte"``, ``"any"``).
        file_structure: Map of step name to expected file path.
        rules: Framework-specific rules (10-14 entries).
        golden_examples: Map of step name to code snippet.
        supports_auto_update: Whether the framework supports auto-update.
        supports_system_tray: Whether the framework supports system tray.
        package_manager: Package manager (``"npm"``, ``"cargo"``).
        platforms: Supported OS platforms.
    """

    name: str                               # "electron", "tauri"
    display_name: str                       # "Electron", "Tauri"
    language: str                           # "typescript", "rust"
    code_block_lang: str                    # For markdown code fences
    backend_language: str                   # "javascript", "rust"
    frontend_framework: str                 # "react", "vue", "svelte", "any"
    file_structure: dict[str, str]          # Step name -> expected file path
    rules: tuple[str, ...]                  # 10-14 framework-specific rules
    golden_examples: dict[str, str]         # Step name -> code snippet
    supports_auto_update: bool
    supports_system_tray: bool
    package_manager: str                    # "npm", "cargo"
    platforms: tuple[str, ...]              # ("windows", "macos", "linux")

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name is required")
        if not self.display_name:
            raise ValueError("display_name is required")
        if len(self.rules) < 5:
            raise ValueError(f"At least 5 rules required, got {len(self.rules)}")
        if not self.platforms:
            raise ValueError("At least one platform required")


# -- Registry ----------------------------------------------------------------

_DESKTOP_REGISTRY: dict[str, DesktopConfig] = {}

_aliases: dict[str, str] = {
    "electronjs": "electron",
    "electron_js": "electron",
    "tauri2": "tauri",
    "tauri_v2": "tauri",
}


def register_desktop(config: DesktopConfig) -> None:
    """Register a desktop framework configuration."""
    if config.name in _DESKTOP_REGISTRY:
        raise ValueError(f"Desktop config '{config.name}' already registered")
    _DESKTOP_REGISTRY[config.name] = config
    logger.debug("desktop_config_registered", extra={"name": config.name})


def get_desktop_config(name: str) -> DesktopConfig:
    """Get desktop config by canonical name or alias (case-insensitive).

    Raises ``KeyError`` for unknown frameworks (no silent fallback).
    """
    key = name.lower().strip().replace(" ", "_").replace(".", "").replace("-", "_")
    resolved = _aliases.get(key, key)
    if resolved not in _DESKTOP_REGISTRY:
        raise KeyError(
            f"Unknown desktop framework '{name}'. "
            f"Available: {sorted(_DESKTOP_REGISTRY.keys())}"
        )
    return _DESKTOP_REGISTRY[resolved]


def list_desktop_frameworks() -> list[str]:
    """List all registered desktop framework names (sorted)."""
    return sorted(_DESKTOP_REGISTRY.keys())


# -- Auto-register all desktop configs on import ------------------------------

from app.agents.desktop_configs import configs as _configs  # noqa: E402, F401

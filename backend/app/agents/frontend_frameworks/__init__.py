"""Frontend framework configuration registry for frontend code generation.

Each supported frontend framework has a ``FrontendFrameworkConfig`` that tells
Aanya how to generate code for that stack:
- Component patterns (functional components, composition API, etc.)
- State management approach (useState, Pinia, NgRx, signals, etc.)
- Routing strategy (App Router, Vue Router, Angular Router, etc.)
- Golden code examples per generation step
- Expected file structure

New frameworks are added by creating a config file (e.g. ``vue_config.py``)
and calling ``register_frontend_framework()`` at module level.

Usage::

    from app.agents.frontend_frameworks import get_frontend_framework_config

    config = get_frontend_framework_config("vue")
    print(config.language)          # "typescript"
    print(len(config.rules))        # 12
    print(config.golden_examples)   # {"component": "...", "store": "...", ...}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FrontendFrameworkConfig:
    """Immutable configuration for a frontend framework.

    Attributes:
        name: Canonical name (e.g. ``"vue"``, ``"angular"``, ``"svelte"``).
        display_name: Human-readable name (e.g. ``"Vue.js"``).
        language: Programming language (``"typescript"`` always for frontends).
        code_block_lang: Markdown code fence language tag.
        component_extension: File extension for components (e.g. ``.tsx``, ``.vue``, ``.svelte``).
        file_structure: Map of generation step name to expected file path.
        rules: 11-15 mandatory framework-specific rules.
        golden_examples: Map of generation step name to code snippet.
        generation_order: Ordered list of generation steps.
    """

    name: str
    display_name: str
    language: str
    code_block_lang: str
    component_extension: str
    file_structure: dict[str, str]
    rules: tuple[str, ...]
    golden_examples: dict[str, str]
    generation_order: tuple[dict[str, str], ...]


# ── Registry ────────────────────────────────────────────────────────

_FRONTEND_REGISTRY: dict[str, FrontendFrameworkConfig] = {}


def register_frontend_framework(config: FrontendFrameworkConfig) -> None:
    """Register a frontend framework configuration."""
    _FRONTEND_REGISTRY[config.name] = config
    logger.debug("frontend_framework_registered", name=config.name)


def get_frontend_framework_config(name: str) -> FrontendFrameworkConfig:
    """Get frontend framework config by canonical name.

    Falls back to ``"nextjs"`` for unknown frameworks.
    """
    key = name.lower().replace(".", "").replace(" ", "")
    # Try direct match first
    if key in _FRONTEND_REGISTRY:
        return _FRONTEND_REGISTRY[key]

    # Try alias resolution
    _aliases = {
        "vuejs": "vue", "vue.js": "vue", "nuxt": "vue", "nuxtjs": "vue", "nuxt.js": "vue",
        "angular": "angular", "ng": "angular",
        "svelte": "svelte", "sveltekit": "svelte",
        "remix": "remix",
        "astro": "astro",
        "solid": "solid", "solidjs": "solid", "solid.js": "solid",
        "react": "react", "reactjs": "react",
        "nextjs": "nextjs", "next": "nextjs", "next.js": "nextjs",
    }
    resolved = _aliases.get(key, key)
    if resolved in _FRONTEND_REGISTRY:
        return _FRONTEND_REGISTRY[resolved]

    # Fallback
    if "nextjs" in _FRONTEND_REGISTRY:
        logger.warning("unknown_frontend_fallback", requested=name, fallback="nextjs")
        return _FRONTEND_REGISTRY["nextjs"]

    raise ValueError(f"No frontend config for '{name}' and no nextjs fallback")


def list_frontend_frameworks() -> list[str]:
    """List all registered frontend framework names."""
    return sorted(_FRONTEND_REGISTRY.keys())


# ── Auto-register all frontend framework configs on import ──────────

from app.agents.frontend_frameworks import angular_config as _angular  # noqa: E402, F401
from app.agents.frontend_frameworks import astro_config as _astro  # noqa: E402, F401
from app.agents.frontend_frameworks import nextjs_config as _nextjs  # noqa: E402, F401
from app.agents.frontend_frameworks import react_config as _react  # noqa: E402, F401
from app.agents.frontend_frameworks import remix_config as _remix  # noqa: E402, F401
from app.agents.frontend_frameworks import solid_config as _solid  # noqa: E402, F401
from app.agents.frontend_frameworks import svelte_config as _svelte  # noqa: E402, F401
from app.agents.frontend_frameworks import vue_config as _vue  # noqa: E402, F401

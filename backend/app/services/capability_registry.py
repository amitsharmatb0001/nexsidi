"""Framework Capability Registry — Honest detection + alternatives.

PHASE-7: If user asks for Laravel, system must say "unsupported" and suggest
alternatives — NOT silently generate FastAPI. No more lying to users.

Each framework has:
  - support_level: "full" | "partial" | "unsupported"
  - alternatives: what to suggest if unsupported
  - features: what capabilities it supports
  - rules: framework-specific coding rules for prompt injection
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class FrameworkCapability:
    """Capability profile for a backend or frontend framework."""

    name: str
    language: str
    category: str  # "backend" | "frontend" | "mobile" | "database"
    support_level: str  # "full" | "partial" | "unsupported"
    alternatives: list[str] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    rules: list[str] = field(default_factory=list)

    def is_usable(self) -> bool:
        return self.support_level in ("full", "partial")

    def get_honest_response(self) -> str:
        """Generate honest user-facing message about support level."""
        if self.support_level == "unsupported":
            alts = ", ".join(self.alternatives) if self.alternatives else "our supported frameworks"
            return (
                f"We don't support {self.name} ({self.language}) yet. "
                f"Fully supported alternatives: {alts}. "
                f"Would you like to proceed with one of these instead?"
            )
        if self.support_level == "partial":
            return (
                f"{self.name} ({self.language}) has partial support — "
                f"some features may need manual work after generation."
            )
        return f"{self.name} ({self.language}) is fully supported."


# ── Backend Frameworks ───────────────────────────────────────────────

BACKEND_REGISTRY: dict[str, FrameworkCapability] = {
    "fastapi": FrameworkCapability(
        name="FastAPI", language="python", category="backend",
        support_level="full", alternatives=[],
        features={"orm", "auth", "websocket", "graphql", "openapi", "async", "middleware", "cors"},
        rules=[
            "Use Pydantic v2 for request/response models",
            "Use async def for all route handlers",
            "Use Depends() for dependency injection",
            "Use SQLAlchemy async session for DB operations",
        ],
    ),
    "django": FrameworkCapability(
        name="Django", language="python", category="backend",
        support_level="full", alternatives=[],
        features={"orm", "auth", "websocket", "admin", "templates", "middleware", "cors"},
        rules=[
            "Use Django REST Framework for API endpoints",
            "Use Django ORM models (not raw SQL)",
            "Use class-based views for CRUD operations",
        ],
    ),
    "express": FrameworkCapability(
        name="Express", language="javascript", category="backend",
        support_level="full", alternatives=[],
        features={"middleware", "websocket", "graphql", "cors", "auth"},
        rules=[
            "Use express.Router() for route modules",
            "Use middleware for auth and error handling",
            "Use Sequelize or Prisma for ORM",
        ],
    ),
    "nestjs": FrameworkCapability(
        name="NestJS", language="typescript", category="backend",
        support_level="full", alternatives=[],
        features={"orm", "auth", "websocket", "graphql", "openapi", "middleware", "cors", "microservices"},
        rules=[
            "Use decorators for routing (@Controller, @Get, @Post)",
            "Use TypeORM or Prisma for database",
            "Use Guards for authentication",
            "Use Pipes for validation",
        ],
    ),
    "flask": FrameworkCapability(
        name="Flask", language="python", category="backend",
        support_level="partial", alternatives=["fastapi"],
        features={"middleware", "auth", "cors"},
        rules=["Use Flask-SQLAlchemy for ORM", "Use Flask-Login for auth"],
    ),
    "spring-boot": FrameworkCapability(
        name="Spring Boot", language="java", category="backend",
        support_level="partial", alternatives=["nestjs"],
        features={"orm", "auth", "websocket", "graphql", "microservices"},
        rules=["Use Spring Data JPA for ORM", "Use Spring Security for auth"],
    ),
    "rails": FrameworkCapability(
        name="Ruby on Rails", language="ruby", category="backend",
        support_level="partial", alternatives=["django"],
        features={"orm", "auth", "websocket", "admin"},
        rules=["Use ActiveRecord for ORM", "Use Devise for auth"],
    ),
    "go-fiber": FrameworkCapability(
        name="Fiber", language="go", category="backend",
        support_level="partial", alternatives=["express"],
        features={"middleware", "websocket", "cors"},
        rules=["Use GORM for ORM", "Use Go modules"],
    ),
    # ── Honestly Unsupported ──
    "laravel": FrameworkCapability(
        name="Laravel", language="php", category="backend",
        support_level="unsupported",
        alternatives=["fastapi", "django", "nestjs"],
        features=set(),
    ),
    "asp.net": FrameworkCapability(
        name="ASP.NET", language="csharp", category="backend",
        support_level="unsupported",
        alternatives=["nestjs", "spring-boot"],
        features=set(),
    ),
    "phoenix": FrameworkCapability(
        name="Phoenix", language="elixir", category="backend",
        support_level="unsupported",
        alternatives=["fastapi", "express"],
        features=set(),
    ),
    "deno": FrameworkCapability(
        name="Deno/Fresh", language="typescript", category="backend",
        support_level="unsupported",
        alternatives=["nestjs", "express"],
        features=set(),
    ),
}

# ── Frontend Frameworks ──────────────────────────────────────────────

FRONTEND_REGISTRY: dict[str, FrameworkCapability] = {
    "react": FrameworkCapability(
        name="React", language="javascript", category="frontend",
        support_level="full", alternatives=[],
        features={"hooks", "context", "ssr", "routing", "state_management"},
    ),
    "nextjs": FrameworkCapability(
        name="Next.js", language="typescript", category="frontend",
        support_level="full", alternatives=[],
        features={"hooks", "ssr", "ssg", "api_routes", "routing", "state_management"},
    ),
    "vue": FrameworkCapability(
        name="Vue.js", language="javascript", category="frontend",
        support_level="full", alternatives=[],
        features={"composition_api", "routing", "state_management", "ssr"},
    ),
    "nuxt": FrameworkCapability(
        name="Nuxt", language="typescript", category="frontend",
        support_level="full", alternatives=[],
        features={"ssr", "ssg", "routing", "state_management", "api_routes"},
    ),
    "angular": FrameworkCapability(
        name="Angular", language="typescript", category="frontend",
        support_level="partial", alternatives=["react", "nextjs"],
        features={"routing", "state_management", "forms", "di"},
    ),
    "svelte": FrameworkCapability(
        name="Svelte/SvelteKit", language="javascript", category="frontend",
        support_level="partial", alternatives=["react", "vue"],
        features={"routing", "ssr"},
    ),
}

# ── Mobile Frameworks ────────────────────────────────────────────────

MOBILE_REGISTRY: dict[str, FrameworkCapability] = {
    "react-native": FrameworkCapability(
        name="React Native", language="javascript", category="mobile",
        support_level="full", alternatives=[],
        features={"cross_platform", "hot_reload", "native_modules"},
    ),
    "expo": FrameworkCapability(
        name="Expo", language="javascript", category="mobile",
        support_level="full", alternatives=[],
        features={"cross_platform", "hot_reload", "ota_updates", "managed_workflow"},
    ),
    "flutter": FrameworkCapability(
        name="Flutter", language="dart", category="mobile",
        support_level="partial", alternatives=["react-native", "expo"],
        features={"cross_platform", "hot_reload", "widgets"},
    ),
    "swift": FrameworkCapability(
        name="SwiftUI", language="swift", category="mobile",
        support_level="partial", alternatives=["react-native"],
        features={"ios_native", "widgets"},
    ),
    "kotlin": FrameworkCapability(
        name="Kotlin/Jetpack Compose", language="kotlin", category="mobile",
        support_level="partial", alternatives=["react-native"],
        features={"android_native", "compose"},
    ),
}

# ── Database Frameworks ──────────────────────────────────────────────

DATABASE_REGISTRY: dict[str, FrameworkCapability] = {
    "postgresql": FrameworkCapability(
        name="PostgreSQL", language="sql", category="database",
        support_level="full", alternatives=[],
        features={"jsonb", "full_text_search", "rls", "triggers", "extensions"},
    ),
    "mysql": FrameworkCapability(
        name="MySQL", language="sql", category="database",
        support_level="full", alternatives=[],
        features={"full_text_search", "json", "replication"},
    ),
    "mongodb": FrameworkCapability(
        name="MongoDB", language="nosql", category="database",
        support_level="partial", alternatives=["postgresql"],
        features={"document_store", "aggregation", "change_streams"},
    ),
    "sqlite": FrameworkCapability(
        name="SQLite", language="sql", category="database",
        support_level="full", alternatives=[],
        features={"embedded", "json"},
    ),
}

# ── Combined Registry ────────────────────────────────────────────────

ALL_REGISTRIES: dict[str, dict[str, FrameworkCapability]] = {
    "backend": BACKEND_REGISTRY,
    "frontend": FRONTEND_REGISTRY,
    "mobile": MOBILE_REGISTRY,
    "database": DATABASE_REGISTRY,
}


# ── Detection ────────────────────────────────────────────────────────

# Keyword → framework name mapping for detection
_DETECTION_KEYWORDS: dict[str, tuple[str, str]] = {
    # (keyword, (framework_key, category))
    "laravel": ("laravel", "backend"),
    "php": ("laravel", "backend"),
    "fastapi": ("fastapi", "backend"),
    "fast api": ("fastapi", "backend"),
    "django": ("django", "backend"),
    "express": ("express", "backend"),
    "expressjs": ("express", "backend"),
    "express.js": ("express", "backend"),
    "nestjs": ("nestjs", "backend"),
    "nest.js": ("nestjs", "backend"),
    "flask": ("flask", "backend"),
    "spring boot": ("spring-boot", "backend"),
    "spring": ("spring-boot", "backend"),
    "rails": ("rails", "backend"),
    "ruby on rails": ("rails", "backend"),
    "golang": ("go-fiber", "backend"),
    "go fiber": ("go-fiber", "backend"),
    "asp.net": ("asp.net", "backend"),
    ".net": ("asp.net", "backend"),
    "c#": ("asp.net", "backend"),
    "csharp": ("asp.net", "backend"),
    "phoenix": ("phoenix", "backend"),
    "elixir": ("phoenix", "backend"),
    "deno": ("deno", "backend"),
    # Frontend
    "react": ("react", "frontend"),
    "reactjs": ("react", "frontend"),
    "next.js": ("nextjs", "frontend"),
    "nextjs": ("nextjs", "frontend"),
    "next js": ("nextjs", "frontend"),
    "vue": ("vue", "frontend"),
    "vuejs": ("vue", "frontend"),
    "nuxt": ("nuxt", "frontend"),
    "nuxtjs": ("nuxt", "frontend"),
    "angular": ("angular", "frontend"),
    "svelte": ("svelte", "frontend"),
    "sveltekit": ("svelte", "frontend"),
    # Mobile
    "react native": ("react-native", "mobile"),
    "expo": ("expo", "mobile"),
    "flutter": ("flutter", "mobile"),
    "swift": ("swift", "mobile"),
    "swiftui": ("swift", "mobile"),
    "kotlin": ("kotlin", "mobile"),
    "jetpack compose": ("kotlin", "mobile"),
    # Database
    "postgresql": ("postgresql", "database"),
    "postgres": ("postgresql", "database"),
    "mysql": ("mysql", "database"),
    "mongodb": ("mongodb", "database"),
    "mongo": ("mongodb", "database"),
    "sqlite": ("sqlite", "database"),
}


def detect_frameworks(
    requirements_text: str,
) -> dict[str, FrameworkCapability | None]:
    """Detect all requested frameworks from user requirements text.

    Returns dict with keys: backend, frontend, mobile, database.
    Value is None if no framework detected for that category.
    """
    text_lower = requirements_text.lower()
    detected: dict[str, FrameworkCapability | None] = {
        "backend": None,
        "frontend": None,
        "mobile": None,
        "database": None,
    }

    for keyword, (framework_key, category) in _DETECTION_KEYWORDS.items():
        if keyword in text_lower:
            registry = ALL_REGISTRIES.get(category, {})
            cap = registry.get(framework_key)
            if cap and (detected[category] is None):
                detected[category] = cap
                logger.info(
                    "framework_detected",
                    keyword=keyword,
                    framework=cap.name,
                    category=category,
                    support=cap.support_level,
                )

    return detected


def get_capability(
    framework_name: str, category: str = "backend"
) -> FrameworkCapability | None:
    """Look up a specific framework capability."""
    registry = ALL_REGISTRIES.get(category, {})
    return registry.get(framework_name.lower().replace(" ", "-"))


def get_unsupported_warnings(
    detected: dict[str, FrameworkCapability | None],
) -> list[str]:
    """Get honest warning messages for any unsupported frameworks."""
    warnings = []
    for category, cap in detected.items():
        if cap and not cap.is_usable():
            warnings.append(cap.get_honest_response())
    return warnings


def get_all_supported(category: str = "backend") -> list[FrameworkCapability]:
    """Get all frameworks with full or partial support for a category."""
    registry = ALL_REGISTRIES.get(category, {})
    return [cap for cap in registry.values() if cap.is_usable()]

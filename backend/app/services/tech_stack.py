"""Tech stack validation, suggestions, and maturity registry.

Validates user-requested tech stacks against what NexSidi agents can actually
generate.  Suggests optimal stacks based on project requirements.

Maturity levels:
- production: Full template + agent support, battle-tested
- beta: Working but limited templates, may have gaps
- alpha: Experimental, basic generation only
- planned: Not yet supported, will substitute with default

Usage:
    from app.services.tech_stack import validate_tech_stack, suggest_tech_stack

    result = validate_tech_stack({"backend": "FastAPI", "frontend": "Vue.js"})
    # result.supported = True, result.warnings = ["Vue.js is in beta..."]

    suggestion = suggest_tech_stack(requirements_text="ACID-heavy financial app")
    # suggestion = {"backend": "FastAPI", "frontend": "Next.js", "database": "PostgreSQL"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# -- Maturity Levels ----------------------------------------------------------


class Maturity(str, Enum):
    PRODUCTION = "production"  # Full support, tested
    BETA = "beta"  # Working but limited
    ALPHA = "alpha"  # Experimental
    PLANNED = "planned"  # Not yet, will substitute


# -- Tech Stack Registry ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TechEntry:
    """A single technology in the registry."""

    canonical_name: str  # Display name (e.g., "FastAPI")
    category: str  # "backend", "frontend", "database", "cache", etc.
    maturity: Maturity
    aliases: tuple[str, ...] = ()  # Alternative names users might type
    default_substitute: str | None = None  # What to use if unsupported


# Backend frameworks
BACKEND_STACKS: dict[str, TechEntry] = {
    "fastapi": TechEntry("FastAPI", "backend", Maturity.PRODUCTION, ("fast-api", "fast_api")),
    "django": TechEntry("Django", "backend", Maturity.PRODUCTION, ("django-rest", "drf")),
    "express": TechEntry("Express.js", "backend", Maturity.PRODUCTION, ("express.js", "expressjs", "node")),
    "flask": TechEntry("Flask", "backend", Maturity.ALPHA, (), "fastapi"),
    "spring": TechEntry("Spring Boot", "backend", Maturity.PLANNED, ("spring-boot", "springboot", "java"), "fastapi"),
    "rails": TechEntry("Ruby on Rails", "backend", Maturity.PLANNED, ("ruby-on-rails", "ror", "ruby"), "fastapi"),
    "laravel": TechEntry("Laravel", "backend", Maturity.PLANNED, ("php",), "fastapi"),
    "gin": TechEntry("Gin", "backend", Maturity.PLANNED, ("go", "golang"), "fastapi"),
    "nestjs": TechEntry("NestJS", "backend", Maturity.PLANNED, ("nest",), "express"),
    "actix": TechEntry("Actix Web", "backend", Maturity.PLANNED, ("rust", "actix-web"), "fastapi"),
}

# Frontend frameworks
FRONTEND_STACKS: dict[str, TechEntry] = {
    "nextjs": TechEntry("Next.js", "frontend", Maturity.PRODUCTION, ("next.js", "next", "nextjs15")),
    "react": TechEntry("React", "frontend", Maturity.BETA, ("react.js", "reactjs", "react19")),
    "vue": TechEntry("Vue.js", "frontend", Maturity.ALPHA, ("vue.js", "vuejs", "vue3"), "nextjs"),
    "angular": TechEntry("Angular", "frontend", Maturity.PLANNED, ("ng",), "nextjs"),
    "svelte": TechEntry("SvelteKit", "frontend", Maturity.PLANNED, ("svelte", "sveltekit"), "nextjs"),
    "remix": TechEntry("Remix", "frontend", Maturity.PLANNED, (), "nextjs"),
    "nuxt": TechEntry("Nuxt.js", "frontend", Maturity.PLANNED, ("nuxt", "nuxt3"), "nextjs"),
}

# Databases
DATABASE_STACKS: dict[str, TechEntry] = {
    "postgresql": TechEntry("PostgreSQL", "database", Maturity.PRODUCTION, ("postgres", "pg", "psql")),
    "mysql": TechEntry("MySQL", "database", Maturity.PRODUCTION, ("mariadb", "maria")),
    "mongodb": TechEntry("MongoDB", "database", Maturity.BETA, ("mongo",)),
    "sqlite": TechEntry("SQLite", "database", Maturity.PRODUCTION, ("sqlite3",)),
    "firebase": TechEntry("Firebase Firestore", "database", Maturity.BETA, ("firestore", "firebase_firestore")),
    "supabase": TechEntry("Supabase", "database", Maturity.BETA, ()),
    "dynamodb": TechEntry("DynamoDB", "database", Maturity.BETA, ("dynamo", "aws_dynamodb")),
    "neo4j": TechEntry("Neo4j", "database", Maturity.ALPHA, ("neo4j_graph", "graph")),
    "cockroachdb": TechEntry("CockroachDB", "database", Maturity.ALPHA, ("cockroach", "crdb")),
    "redis_primary": TechEntry("Redis (Primary)", "database", Maturity.BETA, ("redis_db",)),
}

# Cache / Message Queue
INFRA_STACKS: dict[str, TechEntry] = {
    "redis": TechEntry("Redis/Valkey", "cache", Maturity.PRODUCTION, ("valkey",)),
    "rabbitmq": TechEntry("RabbitMQ", "queue", Maturity.PLANNED, ("rabbit",), "redis"),
    "kafka": TechEntry("Kafka", "queue", Maturity.PLANNED, (), "redis"),
}

# Mobile frameworks
MOBILE_STACKS: dict[str, TechEntry] = {
    "react_native": TechEntry("React Native", "mobile", Maturity.BETA, ("rn", "react-native")),
    "flutter": TechEntry("Flutter", "mobile", Maturity.BETA, ("dart",)),
    "swift": TechEntry("SwiftUI", "mobile", Maturity.ALPHA, ("swiftui", "ios")),
    "kotlin_compose": TechEntry("Kotlin Compose", "mobile", Maturity.ALPHA, ("android", "compose", "jetpack")),
    "expo": TechEntry("Expo", "mobile", Maturity.BETA, ("expo-react-native",)),
}

# Desktop frameworks
DESKTOP_STACKS: dict[str, TechEntry] = {
    "electron": TechEntry("Electron", "desktop", Maturity.BETA, ("electronjs",)),
    "tauri": TechEntry("Tauri", "desktop", Maturity.ALPHA, ("tauri2",)),
}

# Combined registry (all entries by lowercase key)
ALL_STACKS: dict[str, TechEntry] = {
    **BACKEND_STACKS,
    **FRONTEND_STACKS,
    **DATABASE_STACKS,
    **INFRA_STACKS,
    **MOBILE_STACKS,
    **DESKTOP_STACKS,
}

# Build alias lookup: alias -> canonical key
_ALIAS_LOOKUP: dict[str, str] = {}
for key, entry in ALL_STACKS.items():
    _ALIAS_LOOKUP[key] = key
    _ALIAS_LOOKUP[entry.canonical_name.lower()] = key
    for alias in entry.aliases:
        _ALIAS_LOOKUP[alias.lower()] = key


def resolve_tech_name(name: str) -> str | None:
    """Resolve a tech name (including aliases) to its canonical registry key.

    Returns None if the name is not recognized.
    """
    return _ALIAS_LOOKUP.get(name.lower().strip())


# -- Validation ---------------------------------------------------------------


@dataclass(slots=True)
class ValidationResult:
    """Result of tech stack validation."""

    supported: bool = True
    warnings: list[str] = field(default_factory=list)
    substitutions: dict[str, str] = field(default_factory=dict)
    resolved: dict[str, str] = field(default_factory=dict)


def validate_tech_stack(tech_stack: dict[str, str]) -> ValidationResult:
    """Validate a user-requested tech stack against the registry.

    Args:
        tech_stack: Dict like {"backend": "FastAPI", "frontend": "Vue.js", "database": "PostgreSQL"}

    Returns:
        ValidationResult with warnings, substitutions, and resolved names.
    """
    result = ValidationResult()

    for category, name in tech_stack.items():
        key = resolve_tech_name(name)

        if key is None:
            # Unknown technology
            result.warnings.append(
                f"'{name}' is not recognized. "
                f"It will be substituted with the default for {category}."
            )
            # Substitute with category default
            defaults = {"backend": "FastAPI", "frontend": "Next.js", "database": "PostgreSQL"}
            substitute = defaults.get(category, name)
            result.substitutions[category] = substitute
            result.resolved[category] = substitute
            continue

        entry = ALL_STACKS[key]
        result.resolved[category] = entry.canonical_name

        if entry.maturity == Maturity.PRODUCTION:
            pass  # Fully supported
        elif entry.maturity == Maturity.BETA:
            result.warnings.append(
                f"{entry.canonical_name} is in beta. "
                "Templates may have gaps. Consider using the production default."
            )
        elif entry.maturity == Maturity.ALPHA:
            result.warnings.append(
                f"{entry.canonical_name} is in alpha (experimental). "
                "Only basic code generation is supported."
            )
        elif entry.maturity == Maturity.PLANNED:
            substitute_key = entry.default_substitute or "fastapi"
            substitute_entry = ALL_STACKS.get(substitute_key)
            substitute_name = substitute_entry.canonical_name if substitute_entry else substitute_key

            result.warnings.append(
                f"{entry.canonical_name} is not yet supported. "
                f"Substituting with {substitute_name}."
            )
            result.substitutions[category] = substitute_name
            result.resolved[category] = substitute_name
            result.supported = False

    return result


# -- Suggestions --------------------------------------------------------------


@dataclass(slots=True)
class TechSuggestion:
    """A suggested tech stack based on project requirements."""

    stack: dict[str, str]
    reasoning: dict[str, str]


def suggest_tech_stack(
    requirements_text: str = "",
    project_type: str = "",
    scale: str = "small",
) -> TechSuggestion:
    """Suggest an optimal tech stack based on project requirements.

    Uses heuristic rules to match requirements to the best supported stack.

    Args:
        requirements_text: User's requirements description
        project_type: "api", "webapp", "ecommerce", "saas", "mobile-backend", etc.
        scale: "small", "medium", "large", "enterprise"

    Returns:
        TechSuggestion with stack and reasoning.
    """
    text = (requirements_text + " " + project_type).lower()
    stack: dict[str, str] = {}
    reasoning: dict[str, str] = {}

    # Backend selection
    if any(word in text for word in ["enterprise", "admin", "cms", "content management"]):
        stack["backend"] = "Django"
        reasoning["backend"] = "Django's admin panel and ORM suit enterprise/CMS apps"
    elif any(word in text for word in ["realtime", "real-time", "websocket", "chat", "gaming"]):
        stack["backend"] = "Express.js"
        reasoning["backend"] = "Node.js excels at real-time WebSocket applications"
    elif any(word in text for word in ["ml", "machine learning", "ai", "data science", "python"]):
        stack["backend"] = "FastAPI"
        reasoning["backend"] = "FastAPI integrates natively with Python ML libraries"
    else:
        stack["backend"] = "FastAPI"
        reasoning["backend"] = "FastAPI: fast, modern, async, excellent for APIs"

    # Frontend selection
    if any(word in text for word in ["seo", "blog", "marketing", "content", "ssr"]):
        stack["frontend"] = "Next.js"
        reasoning["frontend"] = "Next.js SSR provides excellent SEO and performance"
    elif any(word in text for word in ["dashboard", "admin", "internal", "crm"]):
        stack["frontend"] = "React"
        reasoning["frontend"] = "React SPA works well for dashboards and internal tools"
    elif any(word in text for word in ["simple", "lightweight", "prototype", "mvp"]):
        stack["frontend"] = "React"
        reasoning["frontend"] = "React is fastest for prototyping and MVPs"
    else:
        stack["frontend"] = "Next.js"
        reasoning["frontend"] = "Next.js: full-featured React framework with SSR"

    # Database selection
    if any(word in text for word in ["acid", "financial", "banking", "transaction", "payment"]):
        stack["database"] = "PostgreSQL"
        reasoning["database"] = "PostgreSQL: ACID compliance critical for financial data"
    elif any(word in text for word in ["document", "flexible schema", "nosql", "unstructured"]):
        stack["database"] = "MongoDB"
        reasoning["database"] = "MongoDB suits flexible-schema document storage (alpha support)"
    elif any(word in text for word in ["embedded", "serverless", "edge", "offline"]):
        stack["database"] = "SQLite"
        reasoning["database"] = "SQLite: zero-config, embedded, good for serverless/edge"
    else:
        stack["database"] = "PostgreSQL"
        reasoning["database"] = "PostgreSQL: versatile, reliable, production-proven"

    # Cache selection
    if any(word in text for word in ["cache", "session", "queue", "pubsub", "real-time"]):
        stack["cache"] = "Redis/Valkey"
        reasoning["cache"] = "Redis/Valkey for caching, sessions, and pub/sub"
    elif scale in ("large", "enterprise"):
        stack["cache"] = "Redis/Valkey"
        reasoning["cache"] = "Redis/Valkey needed for production-scale caching"

    return TechSuggestion(stack=stack, reasoning=reasoning)


# -- Convenience Functions ----------------------------------------------------


def get_supported_stacks() -> dict[str, list[dict[str, str]]]:
    """Return all supported tech stacks grouped by category."""
    categories: dict[str, list[dict[str, str]]] = {}
    for key, entry in ALL_STACKS.items():
        cat = entry.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            "name": entry.canonical_name,
            "key": key,
            "maturity": entry.maturity.value,
        })
    return categories


def get_maturity(tech_name: str) -> Maturity | None:
    """Get the maturity level of a technology."""
    key = resolve_tech_name(tech_name)
    if key and key in ALL_STACKS:
        return ALL_STACKS[key].maturity
    return None

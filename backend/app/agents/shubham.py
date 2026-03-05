"""Shubham — Backend Engineer: hybrid template + AI code generation.

Shubham generates backend code using a two-phase approach:
1. Template phase (ZERO AI): Dockerfile, docker-compose, requirements.txt,
   .env, database.py, config.py, main.py — all from Jinja2 templates
2. AI phase (agentic tool loop): The LLM receives ONE comprehensive prompt
   listing ALL files to generate. It uses write_file, read_file,
   validate_syntax, list_files, ask_architect, and task_complete tools to
   iteratively produce, inspect, and validate all required source files.

The agentic approach lets the LLM read previously written files before
writing dependent ones, validate Python syntax in-loop, and ask the
architect for clarification — producing more coherent, self-consistent code
than the old per-file call_ai_with_continuation() approach.

Supports: FastAPI (default), Django, Express, Flask, NestJS, Next.js,
          Laravel, Spring Boot, ASP.NET, Go/Gin, Rails, Rust/Axum, Kotlin/Ktor
Security-critical code ALWAYS uses Sonnet 4.6 (AUDIT FIX #17).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai_with_tools,
    estimate_file_complexity,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)

# R37-FIX-23: Cap the total accumulated code injected into prompts to prevent
# quadratic token cost growth.  Each round keeps all prior file contents; without
# this cap a large project would exhaust the context window.
_MAX_ACCUMULATED_CHARS = 100_000  # int; ~25K tokens; safe for 200K-token models

FASTAPI_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/app/models.py", "task_type": "general",
     "description": "SQLAlchemy models from architecture contract tables"},
    {"name": "schemas", "path": "backend/app/schemas.py", "task_type": "general",
     "description": "Pydantic request/response schemas matching models"},
    {"name": "security", "path": "backend/app/security.py", "task_type": "auth_code",
     "description": "Auth middleware, password hashing, JWT handling"},
    {"name": "routers", "path": "backend/app/routers/", "task_type": "general",
     "description": "FastAPI route handlers using schemas and services"},
    {"name": "services", "path": "backend/app/services/", "task_type": "general",
     "description": "Business logic services called by routers"},
    {"name": "tests", "path": "backend/tests/", "task_type": "general",
     "description": "Pytest tests for all endpoints and services"},
    {"name": "seed_db", "path": "backend/scripts/seed.py", "task_type": "general",
     "description": "Database seed script for development"},
]

DJANGO_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/core/models.py", "task_type": "general",
     "description": "Django ORM models from architecture contract tables"},
    {"name": "serializers", "path": "backend/core/serializers.py", "task_type": "general",
     "description": "DRF serializers matching models"},
    {"name": "permissions", "path": "backend/core/permissions.py", "task_type": "auth_code",
     "description": "Custom DRF permissions and auth classes"},
    {"name": "views", "path": "backend/core/views.py", "task_type": "general",
     "description": "DRF viewsets and API views"},
    {"name": "urls", "path": "backend/core/urls.py", "task_type": "general",
     "description": "URL routing with DRF router"},
    {"name": "services", "path": "backend/core/services.py", "task_type": "general",
     "description": "Business logic services called by views"},
    {"name": "tests", "path": "backend/core/tests/", "task_type": "general",
     "description": "Django tests for all endpoints and services"},
    {"name": "management", "path": "backend/core/management/commands/seed.py", "task_type": "general",
     "description": "Django management command for database seeding"},
]

EXPRESS_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "db_schema", "path": "backend/src/db/schema.ts", "task_type": "general",
     "description": "Drizzle ORM schema from architecture contract tables"},
    {"name": "types", "path": "backend/src/types/index.ts", "task_type": "general",
     "description": "Zod schemas and TypeScript types matching DB schema"},
    {"name": "middleware", "path": "backend/src/middleware/auth.ts", "task_type": "auth_code",
     "description": "JWT auth middleware and helpers"},
    {"name": "routes", "path": "backend/src/routes/", "task_type": "general",
     "description": "Express route handlers using types and services"},
    {"name": "services", "path": "backend/src/services/", "task_type": "general",
     "description": "Business logic services called by routes"},
    {"name": "tests", "path": "backend/src/__tests__/", "task_type": "general",
     "description": "Jest tests for all endpoints and services"},
    {"name": "seed", "path": "backend/src/scripts/seed.ts", "task_type": "general",
     "description": "Database seed script for development"},
]

# ── Flask Generation Order ──────────────────────────────────────────

FLASK_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/app/models.py", "task_type": "general",
     "description": "SQLAlchemy models from architecture contract tables"},
    {"name": "schemas", "path": "backend/app/schemas.py", "task_type": "general",
     "description": "Marshmallow schemas matching models"},
    {"name": "auth", "path": "backend/app/auth.py", "task_type": "auth_code",
     "description": "Flask-Login auth, password hashing, JWT handling"},
    {"name": "routes", "path": "backend/app/routes/", "task_type": "general",
     "description": "Flask Blueprints with route handlers"},
    {"name": "services", "path": "backend/app/services/", "task_type": "general",
     "description": "Business logic services called by routes"},
    {"name": "tests", "path": "backend/tests/", "task_type": "general",
     "description": "Pytest tests for all endpoints and services"},
    {"name": "seed", "path": "backend/scripts/seed.py", "task_type": "general",
     "description": "Database seed script for development"},
]

# ── NestJS Generation Order ─────────────────────────────────────────

NESTJS_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "entity", "path": "backend/src/entities/", "task_type": "general",
     "description": "TypeORM entities from architecture contract tables"},
    {"name": "dto", "path": "backend/src/dto/", "task_type": "general",
     "description": "class-validator DTOs matching entities"},
    {"name": "guards", "path": "backend/src/guards/auth.guard.ts", "task_type": "auth_code",
     "description": "NestJS Guards for authentication and authorization"},
    {"name": "controllers", "path": "backend/src/controllers/", "task_type": "general",
     "description": "NestJS Controllers with dependency injection"},
    {"name": "services", "path": "backend/src/services/", "task_type": "general",
     "description": "NestJS Services with business logic"},
    {"name": "modules", "path": "backend/src/modules/", "task_type": "general",
     "description": "NestJS Modules wiring controllers, services, entities"},
    {"name": "tests", "path": "backend/src/__tests__/", "task_type": "general",
     "description": "Jest tests for all controllers and services"},
    {"name": "seed", "path": "backend/src/scripts/seed.ts", "task_type": "general",
     "description": "Database seed script for development"},
]

# ── Next.js API Routes Generation Order ─────────────────────────────

NEXTJS_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "db_schema", "path": "backend/prisma/schema.prisma", "task_type": "general",
     "description": "Prisma schema from architecture contract tables"},
    {"name": "types", "path": "backend/src/types/index.ts", "task_type": "general",
     "description": "Zod schemas and TypeScript types"},
    {"name": "middleware", "path": "backend/src/middleware.ts", "task_type": "auth_code",
     "description": "Next.js middleware for auth and request validation"},
    {"name": "routes", "path": "backend/src/app/api/", "task_type": "general",
     "description": "Next.js App Router API route handlers"},
    {"name": "services", "path": "backend/src/services/", "task_type": "general",
     "description": "Business logic services called by route handlers"},
    {"name": "tests", "path": "backend/src/__tests__/", "task_type": "general",
     "description": "Jest tests for all API routes and services"},
    {"name": "seed", "path": "backend/prisma/seed.ts", "task_type": "general",
     "description": "Prisma seed script for development"},
]

# ── Laravel Generation Order ────────────────────────────────────────

LARAVEL_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/app/Models/", "task_type": "general",
     "description": "Eloquent models from architecture contract tables"},
    {"name": "migrations", "path": "backend/database/migrations/", "task_type": "general",
     "description": "Laravel migrations matching models"},
    {"name": "form_requests", "path": "backend/app/Http/Requests/", "task_type": "general",
     "description": "FormRequest validation classes"},
    {"name": "middleware", "path": "backend/app/Http/Middleware/", "task_type": "auth_code",
     "description": "Auth middleware and custom middleware"},
    {"name": "controllers", "path": "backend/app/Http/Controllers/", "task_type": "general",
     "description": "API Controllers using FormRequests and services"},
    {"name": "services", "path": "backend/app/Services/", "task_type": "general",
     "description": "Business logic services called by controllers"},
    {"name": "tests", "path": "backend/tests/Feature/", "task_type": "general",
     "description": "PHPUnit tests for all endpoints and services"},
    {"name": "seeders", "path": "backend/database/seeders/", "task_type": "general",
     "description": "Database seeder classes for development"},
]

# ── Spring Boot Generation Order ────────────────────────────────────

SPRINGBOOT_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "entity", "path": "backend/src/main/java/com/app/entity/", "task_type": "general",
     "description": "JPA entities from architecture contract tables"},
    {"name": "repository", "path": "backend/src/main/java/com/app/repository/", "task_type": "general",
     "description": "Spring Data JPA repositories"},
    {"name": "dto", "path": "backend/src/main/java/com/app/dto/", "task_type": "general",
     "description": "DTOs with validation annotations"},
    {"name": "security", "path": "backend/src/main/java/com/app/security/", "task_type": "auth_code",
     "description": "Spring Security configuration and JWT handling"},
    {"name": "service", "path": "backend/src/main/java/com/app/service/", "task_type": "general",
     "description": "Service layer with business logic"},
    {"name": "controller", "path": "backend/src/main/java/com/app/controller/", "task_type": "general",
     "description": "REST controllers using services and DTOs"},
    {"name": "tests", "path": "backend/src/test/java/com/app/", "task_type": "general",
     "description": "JUnit tests for all endpoints and services"},
]

# ── ASP.NET Core Generation Order ───────────────────────────────────

ASPNET_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/Models/", "task_type": "general",
     "description": "EF Core entities from architecture contract tables"},
    {"name": "dbcontext", "path": "backend/Data/AppDbContext.cs", "task_type": "general",
     "description": "Entity Framework DbContext configuration"},
    {"name": "dto", "path": "backend/DTOs/", "task_type": "general",
     "description": "DTOs with AutoMapper profiles"},
    {"name": "middleware", "path": "backend/Middleware/", "task_type": "auth_code",
     "description": "Auth middleware and JWT validation"},
    {"name": "services", "path": "backend/Services/", "task_type": "general",
     "description": "Service layer with business logic"},
    {"name": "controllers", "path": "backend/Controllers/", "task_type": "general",
     "description": "API Controllers with DI"},
    {"name": "tests", "path": "backend/Tests/", "task_type": "general",
     "description": "xUnit tests for all controllers and services"},
]

# ── Go (Gin) Generation Order ───────────────────────────────────────

GO_GIN_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/internal/models/", "task_type": "general",
     "description": "GORM models from architecture contract tables"},
    {"name": "middleware", "path": "backend/internal/middleware/", "task_type": "auth_code",
     "description": "Gin middleware for auth, CORS, logging"},
    {"name": "handlers", "path": "backend/internal/handlers/", "task_type": "general",
     "description": "Gin handler functions for API endpoints"},
    {"name": "services", "path": "backend/internal/services/", "task_type": "general",
     "description": "Business logic services called by handlers"},
    {"name": "routes", "path": "backend/internal/routes/routes.go", "task_type": "general",
     "description": "Gin router setup wiring handlers and middleware"},
    {"name": "tests", "path": "backend/internal/handlers/", "task_type": "general",
     "description": "Go tests for all handlers and services"},
    {"name": "seed", "path": "backend/cmd/seed/main.go", "task_type": "general",
     "description": "Database seed command for development"},
]

# ── Ruby on Rails Generation Order ──────────────────────────────────

RAILS_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/app/models/", "task_type": "general",
     "description": "ActiveRecord models from architecture contract tables"},
    {"name": "migrations", "path": "backend/db/migrate/", "task_type": "general",
     "description": "Rails migrations matching models"},
    {"name": "auth_concern", "path": "backend/app/controllers/concerns/authenticatable.rb", "task_type": "auth_code",
     "description": "Authentication concern with JWT, bcrypt, before_action callbacks"},
    {"name": "serializers", "path": "backend/app/serializers/", "task_type": "general",
     "description": "ActiveModel Serializers for API responses"},
    {"name": "controllers", "path": "backend/app/controllers/api/v1/", "task_type": "general",
     "description": "API controllers with strong params"},
    {"name": "services", "path": "backend/app/services/", "task_type": "general",
     "description": "Service objects with business logic"},
    {"name": "routes", "path": "backend/config/routes.rb", "task_type": "general",
     "description": "Rails routes with API namespace"},
    {"name": "tests", "path": "backend/spec/", "task_type": "general",
     "description": "RSpec tests for all endpoints and services"},
    {"name": "seeds", "path": "backend/db/seeds.rb", "task_type": "general",
     "description": "Database seed file for development"},
]

# ── Rust (Axum) Generation Order ────────────────────────────────────

RUST_AXUM_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/src/models/", "task_type": "general",
     "description": "SQLx + Serde models from architecture contract tables"},
    {"name": "error", "path": "backend/src/error.rs", "task_type": "general",
     "description": "AppError type implementing IntoResponse"},
    {"name": "middleware", "path": "backend/src/middleware/", "task_type": "auth_code",
     "description": "Axum middleware for auth and request validation"},
    {"name": "handlers", "path": "backend/src/handlers/", "task_type": "general",
     "description": "Axum handler functions using extractors"},
    {"name": "services", "path": "backend/src/services/", "task_type": "general",
     "description": "Business logic services called by handlers"},
    {"name": "routes", "path": "backend/src/routes.rs", "task_type": "general",
     "description": "Axum Router setup wiring handlers"},
    {"name": "tests", "path": "backend/tests/", "task_type": "general",
     "description": "Integration tests for all endpoints"},
]

# ── Kotlin (Ktor) Generation Order ──────────────────────────────────

KOTLIN_KTOR_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/src/main/kotlin/com/app/models/", "task_type": "general",
     "description": "Exposed tables and entity classes"},
    {"name": "dto", "path": "backend/src/main/kotlin/com/app/dto/", "task_type": "general",
     "description": "kotlinx.serialization data classes"},
    {"name": "auth", "path": "backend/src/main/kotlin/com/app/plugins/Authentication.kt", "task_type": "auth_code",
     "description": "Ktor JWT authentication plugin configuration"},
    {"name": "routes", "path": "backend/src/main/kotlin/com/app/routes/", "task_type": "general",
     "description": "Ktor routing DSL with request handling"},
    {"name": "services", "path": "backend/src/main/kotlin/com/app/services/", "task_type": "general",
     "description": "Business logic services with Exposed transactions"},
    {"name": "repository", "path": "backend/src/main/kotlin/com/app/repository/", "task_type": "general",
     "description": "Exposed DAO repository pattern"},
    {"name": "tests", "path": "backend/src/test/kotlin/com/app/", "task_type": "general",
     "description": "Ktor test engine tests for all routes"},
]

# Backward compatibility alias
BACKEND_GENERATION_ORDER = FASTAPI_GENERATION_ORDER

# Framework -> generation order mapping
_GENERATION_ORDERS: dict[str, list[dict[str, str]]] = {
    "fastapi": FASTAPI_GENERATION_ORDER,
    "django": DJANGO_GENERATION_ORDER,
    "express": EXPRESS_GENERATION_ORDER,
    "flask": FLASK_GENERATION_ORDER,
    "nestjs": NESTJS_GENERATION_ORDER,
    "nextjs": NEXTJS_GENERATION_ORDER,
    "laravel": LARAVEL_GENERATION_ORDER,
    "springboot": SPRINGBOOT_GENERATION_ORDER,
    "aspnet": ASPNET_GENERATION_ORDER,
    "go_gin": GO_GIN_GENERATION_ORDER,
    "rails": RAILS_GENERATION_ORDER,
    "rust_axum": RUST_AXUM_GENERATION_ORDER,
    "kotlin_ktor": KOTLIN_KTOR_GENERATION_ORDER,
}


# OCP-FIX: Deprecated — use fw_config.generation_order instead.
# Kept for backward compatibility with tests. Will be removed in next cleanup pass.
def get_backend_generation_order(framework: str = "fastapi") -> list[dict[str, str]]:
    """Get the generation order for a specific backend framework."""
    return _GENERATION_ORDERS.get(framework.lower(), FASTAPI_GENERATION_ORDER)


# ── Dependency Graph for Parallel Generation ───────────────────────
#
# Steps with no dependencies on each other can be generated in parallel.
# The dependency graph maps step_name → set of step_names it depends on.
# Steps sharing the same "level" (all deps satisfied) run concurrently.

FASTAPI_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "schemas": {"models"},
    "security": {"models", "schemas"},
    "routers": {"models", "schemas", "security"},
    "services": {"models", "schemas", "security"},
    "tests": {"models", "schemas", "security", "routers", "services"},
    "seed_db": {"models"},
}

DJANGO_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "serializers": {"models"},
    "permissions": {"models", "serializers"},
    "views": {"models", "serializers", "permissions"},
    "urls": {"views"},
    "services": {"models", "serializers", "permissions"},
    "tests": {"models", "serializers", "permissions", "views", "urls", "services"},
    "management": {"models"},
}

EXPRESS_DEPENDENCIES: dict[str, set[str]] = {
    "db_schema": set(),
    "types": {"db_schema"},
    "middleware": {"db_schema", "types"},
    "routes": {"db_schema", "types", "middleware"},
    "services": {"db_schema", "types", "middleware"},
    "tests": {"db_schema", "types", "middleware", "routes", "services"},
    "seed": {"db_schema"},
}

FLASK_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "schemas": {"models"},
    "auth": {"models", "schemas"},
    "routes": {"models", "schemas", "auth"},
    "services": {"models", "schemas", "auth"},
    "tests": {"models", "schemas", "auth", "routes", "services"},
    "seed": {"models"},
}

NESTJS_DEPENDENCIES: dict[str, set[str]] = {
    "entity": set(),
    "dto": {"entity"},
    "guards": {"entity", "dto"},
    "controllers": {"entity", "dto", "guards"},
    "services": {"entity", "dto", "guards"},
    "modules": {"entity", "dto", "guards", "controllers", "services"},
    "tests": {"entity", "dto", "guards", "controllers", "services", "modules"},
    "seed": {"entity"},
}

NEXTJS_DEPENDENCIES: dict[str, set[str]] = {
    "db_schema": set(),
    "types": {"db_schema"},
    "middleware": {"db_schema", "types"},
    "routes": {"db_schema", "types", "middleware"},
    "services": {"db_schema", "types", "middleware"},
    "tests": {"db_schema", "types", "middleware", "routes", "services"},
    "seed": {"db_schema"},
}

LARAVEL_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "migrations": {"models"},
    "form_requests": {"models"},
    "middleware": {"models"},
    "controllers": {"models", "form_requests", "middleware"},
    "services": {"models", "form_requests"},
    "tests": {"models", "migrations", "form_requests", "middleware", "controllers", "services"},
    "seeders": {"models"},
}

SPRINGBOOT_DEPENDENCIES: dict[str, set[str]] = {
    "entity": set(),
    "repository": {"entity"},
    "dto": {"entity"},
    "security": {"entity", "repository"},
    "service": {"entity", "repository", "dto"},
    "controller": {"entity", "repository", "dto", "security", "service"},
    "tests": {"entity", "repository", "dto", "security", "service", "controller"},
}

ASPNET_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "dbcontext": {"models"},
    "dto": {"models"},
    "middleware": {"models", "dbcontext"},
    "services": {"models", "dbcontext", "dto"},
    "controllers": {"models", "dbcontext", "dto", "middleware", "services"},
    "tests": {"models", "dbcontext", "dto", "middleware", "services", "controllers"},
}

GO_GIN_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "middleware": {"models"},
    "handlers": {"models", "middleware"},
    "services": {"models", "middleware"},
    "routes": {"models", "middleware", "handlers", "services"},
    "tests": {"models", "middleware", "handlers", "services", "routes"},
    "seed": {"models"},
}

RAILS_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "migrations": {"models"},
    "auth_concern": {"models"},
    "serializers": {"models"},
    "controllers": {"models", "auth_concern", "serializers"},
    "services": {"models", "serializers"},
    "routes": {"controllers"},
    "tests": {"models", "migrations", "auth_concern", "serializers", "controllers", "services", "routes"},
    "seeds": {"models"},
}

RUST_AXUM_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "error": set(),
    "middleware": {"models", "error"},
    "handlers": {"models", "error", "middleware"},
    "services": {"models", "error"},
    "routes": {"models", "error", "middleware", "handlers", "services"},
    "tests": {"models", "error", "middleware", "handlers", "services", "routes"},
}

KOTLIN_KTOR_DEPENDENCIES: dict[str, set[str]] = {
    "models": set(),
    "dto": {"models"},
    "auth": {"models", "dto"},
    "routes": {"models", "dto", "auth"},
    "services": {"models", "dto", "auth"},
    "repository": {"models", "dto"},
    "tests": {"models", "dto", "auth", "routes", "services", "repository"},
}

_DEPENDENCY_GRAPHS: dict[str, dict[str, set[str]]] = {
    "fastapi": FASTAPI_DEPENDENCIES,
    "django": DJANGO_DEPENDENCIES,
    "express": EXPRESS_DEPENDENCIES,
    "flask": FLASK_DEPENDENCIES,
    "nestjs": NESTJS_DEPENDENCIES,
    "nextjs": NEXTJS_DEPENDENCIES,
    "laravel": LARAVEL_DEPENDENCIES,
    "springboot": SPRINGBOOT_DEPENDENCIES,
    "aspnet": ASPNET_DEPENDENCIES,
    "go_gin": GO_GIN_DEPENDENCIES,
    "rails": RAILS_DEPENDENCIES,
    "rust_axum": RUST_AXUM_DEPENDENCIES,
    "kotlin_ktor": KOTLIN_KTOR_DEPENDENCIES,
}


# OCP-FIX: Deprecated — use fw_config.dependency_graph instead.
# Kept for backward compatibility with tests. Will be removed in next cleanup pass.
def get_dependency_graph(framework: str = "fastapi") -> dict[str, set[str]]:
    """Get the dependency graph for a backend framework."""
    return _DEPENDENCY_GRAPHS.get(framework.lower(), FASTAPI_DEPENDENCIES)


def compute_parallel_levels(
    generation_order: list[dict[str, str]],
    dependencies: dict[str, set[str]],
) -> list[list[dict[str, str]]]:
    """Group generation steps into parallel execution levels.

    Steps within the same level have all their dependencies satisfied by
    prior levels and can run concurrently via ``asyncio.gather()``.

    Args:
        generation_order: Ordered list of generation steps.
        dependencies: Map of step_name → set of dependency step names.

    Returns:
        List of levels, where each level is a list of steps that can run
        in parallel.
    """
    step_map = {s["name"]: s for s in generation_order}
    completed: set[str] = set()
    remaining = {s["name"] for s in generation_order}
    levels: list[list[dict[str, str]]] = []

    while remaining:
        # Find steps whose deps are all in completed
        ready: list[str] = []
        for name in remaining:
            deps = dependencies.get(name, set())
            if deps <= completed:
                ready.append(name)

        if not ready:
            # Safety: break cycle — just add all remaining sequentially
            ready = sorted(remaining)

        level = [step_map[n] for n in ready if n in step_map]
        levels.append(level)
        completed.update(ready)
        remaining -= set(ready)

    return levels


def split_generation_step(
    step: dict[str, str],
    contract: dict[str, Any],
    threshold: int = 500,
) -> list[dict[str, str]]:
    """Split a large generation step into sub-steps if estimated lines exceed threshold.

    When a contract has many tables/endpoints, a single "models" or "services"
    file might be >500 lines. This splits it into multiple files so each
    AI call generates a manageable chunk.

    Args:
        step: Generation step dict with name, path, task_type, description.
        contract: Architecture contract with tables, endpoints, etc.
        threshold: Estimated line count above which we split.

    Returns:
        List of steps — either the original step (if below threshold) or
        multiple sub-steps.
    """
    estimated = estimate_file_complexity(contract, step["name"])

    if estimated <= threshold:
        return [step]

    # Determine how to split
    tables = contract.get("tables", [])
    if not isinstance(tables, list) or len(tables) <= 1:
        return [step]

    # Split by grouping tables
    splittable_steps = {"models", "schemas", "serializers", "db_schema", "types", "services", "tests"}
    if step["name"] not in splittable_steps:
        return [step]

    # Group tables into chunks of ~4
    chunk_size = max(1, 8 // max(1, (estimated // threshold)))
    chunk_size = max(1, min(chunk_size, 4))

    table_chunks: list[list[Any]] = []
    for i in range(0, len(tables), chunk_size):
        table_chunks.append(tables[i:i + chunk_size])

    if len(table_chunks) <= 1:
        return [step]

    sub_steps: list[dict[str, str]] = []
    base_path = step["path"]
    base_name = step["name"]

    for idx, chunk in enumerate(table_chunks):
        table_names = [t.get("name", f"table_{idx}") if isinstance(t, dict) else str(t)
                       for t in chunk]
        suffix = f"_part{idx + 1}"

        # Generate a sub-path (e.g., "backend/app/models_part1.py")
        if base_path.endswith("/"):
            sub_path = f"{base_path}{base_name}{suffix}.py"
        elif base_path.endswith(".py"):
            sub_path = base_path.replace(".py", f"{suffix}.py")
        elif base_path.endswith(".ts"):
            sub_path = base_path.replace(".ts", f"{suffix}.ts")
        else:
            sub_path = f"{base_path}{suffix}"

        sub_steps.append({
            "name": f"{base_name}{suffix}",
            "path": sub_path,
            "task_type": step["task_type"],
            "description": f"{step['description']} (tables: {', '.join(table_names)})",
            "_parent_step": base_name,
            "_table_subset": ",".join(table_names),
        })

    logger.info(
        "step_split",
        step=base_name,
        estimated_lines=estimated,
        sub_steps=len(sub_steps),
    )

    return sub_steps


# ── Agentic Tool Definitions ────────────────────────────────────────
#
# These tools are exposed to the LLM during Phase 2 (AI code generation).
# The LLM calls them in a tool loop to write files, read them back,
# validate syntax, ask the architect for clarification, and signal
# completion — giving it genuine agency over the generation process.

SHUBHAM_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="write_file",
        description="Write a file to the project output. Use this to save generated code.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root (e.g. 'backend/app/models.py')",
                },
                "content": {
                    "type": "string",
                    "description": "Complete file content to write",
                },
            },
            "required": ["path", "content"],
        },
    ),
    ToolDefinition(
        name="read_file",
        description="Read a previously generated file to check its content or build upon it.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read",
                },
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="validate_syntax",
        description="Validate Python syntax of generated code. Returns 'OK' or error message.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of file to validate",
                },
                "content": {
                    "type": "string",
                    "description": "Content to validate (optional, uses written file if omitted)",
                },
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="list_files",
        description="List all files written so far in the project.",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="ask_architect",
        description=(
            "Ask Vikram (architect) a clarifying question about the contract, "
            "requirements, or design decisions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Your question for the architect",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant context for the question",
                },
            },
            "required": ["question"],
        },
    ),
    ToolDefinition(
        name="task_complete",
        description=(
            "Signal that code generation is complete. Call this when all required "
            "files have been written and validated."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Summary of what was generated",
                },
            },
            "required": ["summary"],
        },
    ),
]


# ── Agentic Tool Handler ─────────────────────────────────────────────


class ShubhamToolHandler:
    """Handles tool calls from Shubham's agentic tool loop.

    Maintains an in-memory dict of path -> content that accumulates all
    files written during Phase 2. When the LLM calls task_complete, the
    ``done`` flag is set and ``summary`` records what was generated.
    """

    def __init__(
        self,
        pipeline_run_id: str,
        generated_files: dict[str, str],
        pipeline_context: dict | None = None,
    ) -> None:
        self._pipeline_run_id = pipeline_run_id
        self._files = generated_files  # Shared dict: path -> content
        self._pipeline_context = pipeline_context or {}  # For agent oracle fallback
        self._done = False
        self._summary = ""

    @property
    def done(self) -> bool:
        """True after the LLM calls task_complete."""
        return self._done

    @property
    def summary(self) -> str:
        """Summary provided by the LLM when it called task_complete."""
        return self._summary

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        """Route tool calls to the appropriate handler method."""
        if tool_name == "write_file":
            return await self._write_file(**tool_input)
        elif tool_name == "read_file":
            return await self._read_file(**tool_input)
        elif tool_name == "validate_syntax":
            return await self._validate_syntax(**tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "ask_architect":
            return await self._ask_architect(**tool_input)
        elif tool_name == "task_complete":
            return self._task_complete(**tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    async def _write_file(self, path: str, content: str) -> str:
        self._files[path] = content
        return f"Written {path} ({len(content)} chars)"

    async def _read_file(self, path: str) -> str:
        if path in self._files:
            content = self._files[path]
            if len(content) > 10000:
                return content[:10000] + "\n... [truncated, use validate_syntax for full file]"
            return content
        return f"File not found: {path}. Available: {list(self._files.keys())[:10]}"

    async def _validate_syntax(self, path: str, content: str | None = None) -> str:
        import ast

        code = content or self._files.get(path, "")
        if not code:
            return f"No content for {path}"
        if not path.endswith(".py"):
            return "OK (non-Python file, skipping AST check)"
        try:
            ast.parse(code)
            return "OK"
        except SyntaxError as e:
            return f"SyntaxError at line {e.lineno}: {e.msg}"

    def _list_files(self) -> str:
        if not self._files:
            return "No files written yet"
        lines = [
            f"- {path} ({len(content)} chars)"
            for path, content in self._files.items()
        ]
        return "\n".join(lines)

    async def _ask_architect(self, question: str, context: str = "") -> str:
        # PATH A: Real-time message bus (works when agents run concurrently)
        try:
            from app.services.agent_message_bus import get_agent_message_bus

            bus = get_agent_message_bus()
            answer = await bus.ask(
                from_agent="shubham",
                to_agent="vikram",
                pipeline_run_id=self._pipeline_run_id,
                question=question,
                context={"context": context},
                timeout=10.0,  # Short timeout — fall back to oracle quickly
            )
            return answer
        except Exception:
            pass  # Fall through to oracle

        # PATH B: Context oracle fallback — Vikram ran before us, so look up
        # the completed contract from the pipeline context.
        if self._pipeline_context:
            from app.services.agent_oracle import query_agent_context
            return query_agent_context(self._pipeline_context, "vikram", question)

        return "Architect not available. Proceed with best judgment based on contract."

    def _task_complete(self, summary: str) -> str:
        self._done = True
        self._summary = summary
        return f"Task marked complete: {summary}"


class Shubham:
    """Backend Engineer — hybrid template + AI code generation.

    Phase 1 uses a Jinja2 template engine (zero AI) to generate boilerplate.
    Phase 2 uses a genuine agentic tool loop: the LLM receives one
    comprehensive prompt and uses write_file / read_file / validate_syntax /
    list_files / ask_architect / task_complete tools to produce all required
    source files, self-validate, and signal completion.
    """

    name = "shubham"
    display_name = "Shubham — Backend Engineer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        # tools is a module-level constant — expose it as the instance attribute
        # that call_ai_with_tools() reads via agent.tools.
        pass

    @property
    def tools(self) -> list[ToolDefinition]:
        """All tools available in the Phase 2 agentic tool loop."""
        return SHUBHAM_TOOLS

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate backend code from approved architecture contract.

        Phase 1: Template engine generates boilerplate (zero AI).
        Phase 2: Agentic tool loop — ONE comprehensive prompt, LLM iterates
                 using write_file / read_file / validate_syntax / list_files /
                 ask_architect / task_complete tools until all files are done.
        """
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        dhruv_output = context.get("dhruv", {})
        generated_files: dict[str, str] = {}

        # Detect backend framework from contract
        tech_stack = contract.get("tech_stack", {})
        backend_framework = tech_stack.get("backend", "fastapi").lower()
        # Resolve display names to canonical names
        _fw_aliases = {
            "fast api": "fastapi", "express.js": "express",
            "nest.js": "nestjs", "nest": "nestjs",
            "next.js": "nextjs", "next": "nextjs",
            "spring boot": "springboot", "spring": "springboot",
            "asp.net": "aspnet", "asp.net core": "aspnet", "dotnet": "aspnet",
            "gin": "go_gin", "go": "go_gin", "golang": "go_gin",
            "ruby on rails": "rails", "ruby": "rails", "ror": "rails",
            "axum": "rust_axum", "rust": "rust_axum",
            "ktor": "kotlin_ktor", "kotlin": "kotlin_ktor",
        }
        backend_framework = _fw_aliases.get(backend_framework, backend_framework)

        # Load framework config for rich prompts
        from app.agents.frameworks import get_framework_config

        fw_config = get_framework_config(backend_framework)
        generation_order = list(fw_config.generation_order)  # OCP-FIX: read from plugin

        # REVIEW-FIX: Reorder generation steps by dependency level so the LLM
        # prompt lists root files (no deps) first, then layer by layer.
        # This replaces the hardcoded array order with a topologically sorted order.
        dep_graph = fw_config.dependency_graph or get_dependency_graph(backend_framework)
        if dep_graph:
            levels = compute_parallel_levels(generation_order, dep_graph)
            generation_order = [step for level in levels for step in level]

        # Read user feedback if available (from checkpoint feedback loop)
        user_feedback = context.get("__user_feedback__", "")

        logger.info(
            "backend_framework_detected",
            framework=backend_framework,
            framework_config=fw_config.display_name,
            generation_steps=len(generation_order),
        )

        # ── Phase 1: Template scaffolding (ZERO AI) ──
        try:
            from app.engine.template_engine import get_template_engine

            engine = get_template_engine()
            template_files = engine.render_all(
                contract,
                categories=["backend", "config"],
                framework=backend_framework,
            )

            for f in template_files:
                generated_files[f.path] = f.content

            logger.info(
                "template_phase_complete",
                files_generated=len(template_files),
                framework=backend_framework,
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            logger.warning("template_phase_failed", error=_sanitize_error(exc))

        # ── Phase 2: Agentic tool loop (ONE prompt, LLM drives generation) ──
        # COST-AGG-FIX: Use the shared pipeline-level tracker when available so
        # Shubham's costs are counted toward the aggregate cap across all agents.
        # Falls back to a fresh local tracker if the run is not registered (e.g.,
        # during unit tests or standalone execution).
        from app.services.pipeline import get_run_cost_tracker
        from app.services.ai_router import ProjectCostTracker

        cost_tracker = get_run_cost_tracker(pipeline_run_id) or ProjectCostTracker(pipeline_run_id=pipeline_run_id)

        system_prompt = self._build_agentic_system_prompt(
            contract=contract,
            generation_order=generation_order,
            db_artifacts=dhruv_output.get("database_artifacts", ""),
            fw_config=fw_config,
            template_file_paths=list(generated_files.keys()),
            user_feedback=user_feedback,
        )

        tool_handler = ShubhamToolHandler(
            pipeline_run_id=pipeline_run_id,
            generated_files=generated_files,  # Shared dict — handler writes into it
            pipeline_context=context,  # For agent oracle fallback in ask_architect
        )

        logger.info(
            "agentic_phase_start",
            framework=backend_framework,
            files_to_generate=len(generation_order),
            template_files_available=len(generated_files),
        )

        try:
            response = await call_ai_with_tools(
                self,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Generate a complete {fw_config.display_name} backend application "
                        f"by writing all required files using the write_file tool. "
                        f"Start with models/entities, then build up through auth, "
                        f"business logic, and finally tests. "
                        f"Call task_complete when ALL files are written and validated."
                    ),
                }],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=tool_handler,
                max_tool_rounds=30,  # Generous limit for large projects
            )

            if cost_tracker is not None:
                cost_tracker.record(response, agent_name=self.name, model_key="high")

            logger.info(
                "agentic_phase_complete",
                files_written=len([p for p in generated_files if p not in (
                    [f.path for f in template_files] if "template_files" in locals() else []
                )]),
                tool_handler_done=tool_handler.done,
                model=response.model_used,
                tokens=response.output_tokens,
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            logger.error("agentic_phase_failed", error=_sanitize_error(exc))
            # generated_files may have partial output from tool calls before the
            # exception — keep whatever was written so downstream agents can work
            # with partial results rather than nothing.

        # ── Layer 1: Builder Self-Check ──
        # Validate own output before handing off — catch errors early
        # before downstream reviewers waste AI calls on obvious issues.
        self_check = self._run_self_check(generated_files, contract, backend_framework)

        logger.info(
            "self_check_complete",
            errors=self_check.get("errors", 0),
            warnings=self_check.get("warnings", 0),
            passed=self_check.get("passed", False),
        )

        output = {
            "generated_files": list(generated_files.keys()),
            # R8-FIX: Store actual file contents — ALL downstream agents
            # (karan, navya, deepika, fixer, aarav, docs, git, security)
            # read file_contents to analyze/fix code.
            "file_contents": dict(generated_files),
            "template_files": [f.path for f in template_files] if "template_files" in locals() else [],
            "ai_files": [s["path"] for s in generation_order],
            "file_count": len(generated_files),
            "generation_order": [s["name"] for s in generation_order],
            "backend_framework": backend_framework,
            "agentic_summary": tool_handler.summary if tool_handler.done else "",
            "cost_summary": cost_tracker.summary(),
            "self_check": self_check,
        }

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _run_self_check(
        self,
        generated_files: dict[str, str],
        contract: dict[str, Any],
        backend_framework: str,
    ) -> dict[str, Any]:
        """Layer 1 self-check: validate own output before downstream handoff.

        Checks (no AI — pure deterministic):
        1. Python files have valid syntax (ast.parse)
        2. No placeholder/TODO comments in generated code
        3. Required files from contract exist
        4. Non-empty file contents
        5. Import consistency (basic check)
        """
        import ast
        import re

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        files_checked = 0

        for path, content in generated_files.items():
            if not content or len(content.strip()) < 10:
                errors.append({"file": path, "issue": "File is empty or near-empty"})
                continue

            files_checked += 1

            # 1. Python syntax check
            if path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    errors.append({
                        "file": path,
                        "issue": f"SyntaxError at line {e.lineno}: {e.msg}",
                    })

            # 2. Placeholder/TODO detection
            todo_matches = re.findall(
                r"(?:TODO|FIXME|HACK|XXX|PLACEHOLDER|implement later|add more here|pass\s*#)",
                content, re.IGNORECASE,
            )
            if todo_matches:
                warnings.append({
                    "file": path,
                    "issue": f"Found {len(todo_matches)} placeholder comment(s): {todo_matches[:3]}",
                })

            # 3. Anti-hallucination: check for common LLM artifacts
            if "```python" in content or "```typescript" in content:
                errors.append({
                    "file": path,
                    "issue": "File contains markdown code fences (LLM artifact)",
                })

        # 4. Check required files exist based on contract
        expected_tables = contract.get("tables", [])
        if expected_tables and backend_framework in ("fastapi", "flask"):
            if not any("models" in p for p in generated_files):
                errors.append({
                    "file": "(missing)",
                    "issue": "No models file found but contract defines database tables",
                })

        # REVIEW-FIX: 5. Import dependency ordering check
        # Verify that files importing from other generated files have those
        # dependencies actually generated. Uses basic import pattern matching.
        import re as _re_dep
        file_modules: dict[str, str] = {}  # Map module-like names to paths
        for path in generated_files:
            # e.g., "backend/app/models.py" → "app.models"
            stem = path.replace("/", ".").replace("\\", ".")
            if stem.endswith(".py"):
                stem = stem[:-3]
            file_modules[stem] = path

        for path, content in generated_files.items():
            if not path.endswith(".py"):
                continue
            # Find import statements
            import_lines = _re_dep.findall(
                r"^(?:from\s+(\S+)\s+import|import\s+(\S+))",
                content, _re_dep.MULTILINE,
            )
            for from_mod, import_mod in import_lines:
                mod = from_mod or import_mod
                if not mod:
                    continue
                # Check if this is an intra-project import
                if mod.startswith(("app.", "core.", "src.")):
                    # Check if referenced module exists in generated files
                    matching = any(
                        mod in fm or fm.endswith(mod)
                        for fm in file_modules
                    )
                    if not matching:
                        # It's OK if it's a template file or standard lib
                        if not any(mod in tp for tp in generated_files):
                            warnings.append({
                                "file": path,
                                "issue": f"Imports '{mod}' but no matching generated file found",
                            })

        return {
            "files_checked": files_checked,
            "errors": len(errors),
            "warnings": len(warnings),
            "error_details": errors[:20],  # Cap detail output
            "warning_details": warnings[:20],
            "passed": len(errors) == 0,
        }

    def _build_agentic_system_prompt(
        self,
        contract: dict[str, Any],
        generation_order: list[dict[str, str]],
        db_artifacts: str,
        fw_config: Any,
        template_file_paths: list[str],
        user_feedback: str = "",
    ) -> str:
        """Build ONE comprehensive system prompt for the agentic Phase 2 tool loop.

        Unlike the old per-file prompt, this describes ALL files the LLM must
        generate in a single session. The LLM drives the process by calling
        write_file, read_file, validate_syntax, etc. in whatever order it chooses,
        and signals completion with task_complete.

        Prompt structure:
        1. Role + framework + language
        2. Architecture Contract — single source of truth
        3. Database DDL (if available)
        4. File Structure — expected project layout
        5. Required files to generate (from generation_order)
        6. Mandatory framework rules
        7. Golden examples for first generation step (models/entities)
        8. Tool usage rules — how to use each tool correctly
        9. Template files already available via read_file
        10. Completeness rules
        11. User feedback (if present)
        """
        import orjson

        contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")

        # AUDIT-FIX: Cap serialized contract to prevent token explosion.
        # For very large contracts (>20K chars), extract only the most relevant
        # sections instead of dumping the full nested object.
        _MAX_CONTRACT_CHARS = 20_000
        if len(contract_json) > _MAX_CONTRACT_CHARS:
            _relevant_keys = ["tables", "endpoints", "tech_stack", "app_name", "database", "auth"]
            trimmed = {k: contract[k] for k in _relevant_keys if k in contract}
            contract_json = orjson.dumps(trimmed, option=orjson.OPT_INDENT_2).decode("utf-8")
            # If still too large after key filtering, hard-truncate
            if len(contract_json) > _MAX_CONTRACT_CHARS:
                contract_json = contract_json[:_MAX_CONTRACT_CHARS] + "\n... [truncated at 20000 chars]"

        lang = fw_config.code_block_lang

        prompt_parts: list[str] = []

        # ── 1. Role + Framework ──
        prompt_parts.extend([
            f"You are Shubham, an expert backend engineer at NexSidi.",
            f"Generate a complete **{fw_config.display_name}** backend application.",
            f"Language: {fw_config.language}.",
            "",
        ])

        # ── 2. Architecture Contract ──
        prompt_parts.extend([
            "## Architecture Contract (SINGLE SOURCE OF TRUTH)",
            f"```json\n{contract_json}\n```",
            "",
        ])

        # ── 3. Database DDL (F9-FIX: BINDING CONSTRAINT) ──
        if db_artifacts:
            prompt_parts.extend([
                "## Database DDL — BINDING CONSTRAINT (from Dhruv)",
                "**Your models MUST match this DDL exactly.**",
                "- Table names, column names, types, and constraints are AUTHORITATIVE.",
                "- Do NOT rename columns, add extra columns, or change types.",
                "- If the DDL has `created_at TIMESTAMPTZ`, your model must use `DateTime(timezone=True)`, not `Date`.",
                "- Foreign keys in the DDL define your ORM relationships.",
                "",
                db_artifacts,
                "",
            ])

        # ── 4. File Structure ──
        prompt_parts.append(f"## {fw_config.display_name} Project File Structure")
        for step_name, path in fw_config.file_structure.items():
            prompt_parts.append(f"- `{step_name}` → `{path}`")
        prompt_parts.append("")

        # ── 5. Required Files (grouped by dependency level) ──
        # REVIEW-FIX: Use compute_parallel_levels() to show files in dependency
        # layers. This tells the LLM which files are independent (Level 0) and
        # which depend on previously generated files (Level 1, 2, ...).
        prompt_parts.append("## Files You Must Generate (by dependency level)")
        prompt_parts.append(
            "Generate files level-by-level. Files in the same level are independent "
            "and can be written in any order. Files in later levels depend on earlier ones."
        )

        # Try to get the dependency graph from fw_config or module-level
        _dep_graph = getattr(fw_config, "dependency_graph", None) or {}
        if _dep_graph:
            _levels = compute_parallel_levels(generation_order, _dep_graph)
            for level_idx, level_steps in enumerate(_levels):
                level_names = ", ".join(s["name"] for s in level_steps)
                prompt_parts.append(f"\n### Level {level_idx} ({level_names})")
                for step in level_steps:
                    deps = _dep_graph.get(step["name"], set())
                    dep_note = f" (depends on: {', '.join(sorted(deps))})" if deps else " (no dependencies)"
                    prompt_parts.append(f"- **{step['path']}** — {step['description']}{dep_note}")
        else:
            # Fallback: flat list if no dependency graph
            for step in generation_order:
                prompt_parts.append(f"- **{step['path']}** — {step['description']}")
        prompt_parts.append("")

        # ── 6. Mandatory Rules ──
        prompt_parts.append(f"## MANDATORY {fw_config.display_name} RULES (NEVER VIOLATE)")
        for rule in fw_config.rules:
            prompt_parts.append(rule)
        prompt_parts.append("")

        # ── 7. Golden Examples (first step — models/entities) ──
        first_step_name = generation_order[0]["name"] if generation_order else ""
        golden = fw_config.golden_examples.get(first_step_name)
        if golden:
            prompt_parts.extend([
                f"## GOLDEN EXAMPLE — {first_step_name}",
                f"Follow this EXACT pattern for the first file. Adapt names/fields from contract.",
                f"```{lang}\n{golden}\n```",
                "",
            ])

        # ── 8. Tool Usage Rules ──
        prompt_parts.extend([
            "## Tool Usage Rules",
            "1. Write models/entities FIRST before anything that depends on them.",
            "2. After writing a Python file, call validate_syntax to check for errors.",
            "3. Before writing a file that imports from another, use read_file to verify imports.",
            "4. Use ask_architect if the contract is ambiguous or contradictory.",
            "5. Use list_files to check what you have written so far.",
            "6. Call task_complete ONLY when ALL required files are written and validated.",
            "7. Each file must be COMPLETE — no truncation, no placeholder comments.",
            "8. Every function must be fully implemented with real business logic.",
            f"9. Error comments use `{fw_config.error_comment_prefix}`.",
            "10. Match table/column names EXACTLY from the contract.",
            "",
        ])

        # ── 9. Template Files Already Available ──
        if template_file_paths:
            prompt_parts.append(
                "## Template Files Already Generated (available via read_file)"
            )
            for path in template_file_paths:
                prompt_parts.append(f"- {path}")
            prompt_parts.append(
                "These include Dockerfile, docker-compose, requirements.txt, "
                "database.py, config.py, main.py. Use read_file to inspect them "
                "and ensure your generated code imports from them correctly."
            )
            prompt_parts.append("")

        # ── 10. Completeness Rules ──
        prompt_parts.extend([
            "## COMPLETENESS RULES",
            "- Generate the COMPLETE file. NEVER stop mid-function or mid-class.",
            "- If the file needs 500+ lines, generate ALL of them. No shortcuts.",
            "- Every function must be fully implemented with real logic.",
            "- Every class must have all methods with complete bodies.",
            "- NEVER leave placeholder comments like 'add more here' or 'implement later'.",
            "",
        ])

        # ── 11. User Feedback ──
        if user_feedback:
            prompt_parts.extend([
                "## User Feedback (incorporate into ALL generated files)",
                user_feedback,
                "",
            ])

        return "\n".join(prompt_parts)

    def _build_generation_prompt(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        db_artifacts: str,
        fw_config: Any,
        user_feedback: str = "",
    ) -> str:
        """Backward-compat wrapper — tests call the old per-step signature.

        The new agentic system sends ONE system prompt for ALL files; this shim
        wraps _build_agentic_system_prompt() so existing test assertions still pass.

        R37-FIX-23: Apply _MAX_ACCUMULATED_CHARS budget — only the most recent
        files within the budget are injected, preventing OOM on large projects.
        """
        # Build a generation order from the single step (test compat)
        generation_order = [step]

        # Budget-cap accumulated_code before injecting it (R37-FIX-23)
        budget = _MAX_ACCUMULATED_CHARS
        capped: dict[str, str] = {}
        for path, code in reversed(list(accumulated_code.items())):
            if budget <= 0:
                break
            snippet = code[:budget]
            capped[path] = snippet
            budget -= len(snippet)
        if len(capped) < len(accumulated_code):
            logger.debug(
                "accumulated_code_budget_exceeded",
                total_files=len(accumulated_code),
                included_files=len(capped),
                omitted=len(accumulated_code) - len(capped),
            )

        # Template files list derived from capped accumulated code
        template_file_paths = list(capped.keys())

        return self._build_agentic_system_prompt(
            contract=contract,
            generation_order=generation_order,
            db_artifacts=db_artifacts,
            fw_config=fw_config,
            template_file_paths=template_file_paths,
            user_feedback=user_feedback,
        )


# Register the agent
_shubham = Shubham()
register_agent(_shubham)

"""Shubham — Backend Engineer: hybrid template + AI code generation.

Shubham generates backend code using a two-phase approach:
1. Template phase (ZERO AI): Dockerfile, docker-compose, requirements.txt,
   .env, database.py, config.py, main.py — all from Jinja2 templates
2. AI phase (dependency-ordered, framework-aware):
   Each framework has its own generation order, rules, golden examples,
   and file structure defined in ``app.agents.frameworks``.

Each AI-generated file sees the ACTUAL output of previously generated
files (not descriptions — the real code), preventing hallucinated imports.

Uses ``call_ai_with_continuation()`` so large files (1000+ lines) are
generated completely — responses that hit token limits are automatically
continued until the full file is produced.

Supports: FastAPI (default), Django, Express
Security-critical code ALWAYS uses Sonnet 4.6 (AUDIT FIX #17).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai_with_continuation,
    estimate_file_complexity,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)

# Dependency-ordered file generation sequences per backend framework

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


class Shubham:
    """Backend Engineer — hybrid template + AI code generation.

    Uses framework-aware prompts with:
    - Framework-specific mandatory rules (ORM, validation, routing, auth)
    - Golden code examples per generation step
    - Language-aware code fences (Python vs TypeScript)
    - Automatic continuation for large files (no truncation)
    """

    name = "shubham"
    display_name = "Shubham — Backend Engineer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="write_file",
            description="Write a generated code file to the project.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "Complete file content."},
                    "language": {
                        "type": "string",
                        "enum": ["python", "typescript", "javascript", "sql", "yaml", "toml"],
                    },
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a previously generated file for context.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to read."},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_contract",
            description="Read the architecture contract (single source of truth).",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))


    def register_tool(self, tool: "ToolDefinition") -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list["ToolDefinition"]:
        """All registered tools."""
        return list(self._tools.values())

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
        Phase 2: AI generates business logic in dependency order with
                 framework-aware prompts and automatic continuation.
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

        generation_order = get_backend_generation_order(backend_framework)

        # Load framework config for rich prompts
        from app.agents.frameworks import get_framework_config

        fw_config = get_framework_config(backend_framework)

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

        # ── Phase 2: AI code generation (parallel dependency-ordered) ──
        from app.services.ai_router import ProjectCostTracker, select_model_for_generation

        accumulated_code: dict[str, str] = {}
        cost_tracker = ProjectCostTracker(pipeline_run_id=pipeline_run_id)

        # Compute parallel execution levels
        dep_graph = get_dependency_graph(backend_framework)
        levels = compute_parallel_levels(generation_order, dep_graph)

        logger.info(
            "parallel_levels_computed",
            framework=backend_framework,
            levels=len(levels),
            level_sizes=[len(lv) for lv in levels],
        )

        for level_idx, level_steps in enumerate(levels):
            if len(level_steps) == 1:
                # Single step — run sequentially (no overhead from gather)
                step = level_steps[0]
                await self._generate_step(
                    step=step,
                    contract=contract,
                    accumulated_code=accumulated_code,
                    generated_files=generated_files,
                    db_artifacts=dhruv_output.get("database_artifacts", ""),
                    fw_config=fw_config,
                    user_feedback=user_feedback,
                    cost_tracker=cost_tracker,
                )
            else:
                # Multiple steps — run in parallel with asyncio.gather
                logger.info(
                    "parallel_generation",
                    level=level_idx,
                    steps=[s["name"] for s in level_steps],
                )
                # Create a snapshot of accumulated_code so parallel steps share
                # the same context (they don't see each other's output)
                code_snapshot = dict(accumulated_code)
                results = await asyncio.gather(
                    *[
                        self._generate_step_isolated(
                            step=step,
                            contract=contract,
                            accumulated_code=code_snapshot,
                            db_artifacts=dhruv_output.get("database_artifacts", ""),
                            fw_config=fw_config,
                            user_feedback=user_feedback,
                            cost_tracker=cost_tracker,
                        )
                        for step in level_steps
                    ],
                    return_exceptions=True,
                )
                # Merge results back
                for step, result in zip(level_steps, results):
                    if isinstance(result, Exception):
                        from app.services.ai_router import _sanitize_error  # R27-FIX
                        err_prefix = fw_config.error_comment_prefix
                        accumulated_code[step["name"]] = f"{err_prefix} Generation failed: {_sanitize_error(result)}"
                        logger.error("parallel_step_failed", step=step["name"], error=_sanitize_error(result))
                    elif isinstance(result, tuple):
                        name, content = result
                        accumulated_code[name] = content
                        generated_files[step["path"]] = content

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
            "cost_summary": cost_tracker.summary(),
        }

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    async def _generate_step(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        generated_files: dict[str, str],
        db_artifacts: str,
        fw_config: Any,
        user_feedback: str,
        cost_tracker: Any = None,
    ) -> None:
        """Generate a single step, updating accumulated_code and generated_files in place."""
        from app.services.ai_router import select_model_for_generation

        system_prompt = self._build_generation_prompt(
            step=step,
            contract=contract,
            accumulated_code=accumulated_code,
            db_artifacts=db_artifacts,
            fw_config=fw_config,
            user_feedback=user_feedback,
        )

        # Auto-select model based on estimated complexity
        estimated = estimate_file_complexity(contract, step["name"])
        model_key = select_model_for_generation(estimated, step["task_type"])

        try:
            response = await call_ai_with_continuation(self, 
                messages=[{
                    "role": "user",
                    "content": f"Generate the {step['description']} for this project.",
                }],
                system_prompt=system_prompt,
                task_type=step["task_type"],
                temperature=0.1,
                max_continuations=5,
            )

            accumulated_code[step["name"]] = response.content
            generated_files[step["path"]] = response.content

            if cost_tracker is not None:
                cost_tracker.record(response, agent_name=self.name, model_key=model_key)

            logger.info(
                "ai_generation_step",
                step=step["name"],
                model=response.model_used,
                tokens=response.output_tokens,
                was_truncated=response.was_truncated,
                estimated_lines=estimated,
                selected_model=model_key,
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            logger.error("ai_generation_failed", step=step["name"], error=_sanitize_error(exc))
            err_prefix = fw_config.error_comment_prefix
            accumulated_code[step["name"]] = f"{err_prefix} Generation failed: {_sanitize_error(exc)}"

    async def _generate_step_isolated(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        db_artifacts: str,
        fw_config: Any,
        user_feedback: str,
        cost_tracker: Any = None,
    ) -> tuple[str, str]:
        """Generate a single step and return (name, content) without mutating shared state.

        Used for parallel generation via asyncio.gather().
        """
        from app.services.ai_router import select_model_for_generation

        system_prompt = self._build_generation_prompt(
            step=step,
            contract=contract,
            accumulated_code=accumulated_code,
            db_artifacts=db_artifacts,
            fw_config=fw_config,
            user_feedback=user_feedback,
        )

        estimated = estimate_file_complexity(contract, step["name"])
        model_key = select_model_for_generation(estimated, step["task_type"])

        response = await call_ai_with_continuation(self, 
            messages=[{
                "role": "user",
                "content": f"Generate the {step['description']} for this project.",
            }],
            system_prompt=system_prompt,
            task_type=step["task_type"],
            temperature=0.1,
            max_continuations=5,
        )

        if cost_tracker is not None:
            cost_tracker.record(response, agent_name=self.name, model_key=model_key)

        logger.info(
            "ai_generation_step_parallel",
            step=step["name"],
            model=response.model_used,
            tokens=response.output_tokens,
            estimated_lines=estimated,
            selected_model=model_key,
        )

        return (step["name"], response.content)

    def _build_generation_prompt(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        db_artifacts: str,
        fw_config: Any | None = None,
        user_feedback: str = "",
    ) -> str:
        """Build context-rich, framework-aware prompt for each generation step.

        Prompt structure:
        1. WHAT TO BUILD — role + task + framework
        2. Architecture Contract — the single source of truth
        3. Database DDL (if applicable)
        4. File Structure — expected project layout
        5. Mandatory Rules — 14 framework-specific rules
        6. Golden Example — code pattern for this specific step
        7. Previously Generated Files — real code for dependency context
        8. Completeness Rules — never truncate, generate full files
        9. User Feedback (if present)
        10. GENERATE — output format instructions
        """
        import orjson

        # Lazy-import framework config if not provided (backward compat)
        if fw_config is None:
            from app.agents.frameworks import get_framework_config
            fw_config = get_framework_config("fastapi")

        contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")
        lang = fw_config.code_block_lang

        prompt_parts: list[str] = []

        # ── 1. WHAT TO BUILD ──
        prompt_parts.extend([
            f"You are Shubham, the Backend Engineer at NexSidi.",
            f"You are generating the **{step['description']}** for a **{fw_config.display_name}** project.",
            f"Language: {fw_config.language}.",
            "",
        ])

        # ── 2. Architecture Contract ──
        prompt_parts.extend([
            "## Architecture Contract (SINGLE SOURCE OF TRUTH)",
            f"```json\n{contract_json}\n```",
            "",
        ])

        # ── 3. Database DDL (if applicable) ──
        model_steps = ("models", "schemas", "db_schema", "types", "serializers")
        if db_artifacts and step["name"] in model_steps:
            prompt_parts.extend([
                "## Database DDL (from Dhruv)",
                db_artifacts,
                "",
            ])

        # ── 4. File Structure ──
        prompt_parts.append(f"## {fw_config.display_name} Project File Structure")
        for step_name, path in fw_config.file_structure.items():
            prompt_parts.append(f"- `{step_name}` → `{path}`")
        prompt_parts.append("")

        # ── 5. Mandatory Rules ──
        prompt_parts.append(f"## MANDATORY {fw_config.display_name} RULES (NEVER VIOLATE)")
        for rule in fw_config.rules:
            prompt_parts.append(rule)
        prompt_parts.append("")

        # ── 6. Golden Example ──
        golden = fw_config.golden_examples.get(step["name"])
        if golden:
            prompt_parts.extend([
                f"## GOLDEN EXAMPLE — {step['name']}",
                f"Follow this EXACT pattern. Adapt names/fields from the contract.",
                f"```{lang}\n{golden}\n```",
                "",
            ])

        # ── 7. Previously Generated Files ──
        if accumulated_code:
            _MAX_ACCUMULATED_CHARS = 50_000
            prompt_parts.append("## Previously Generated Files (REAL CODE — use exact names)")
            budget = _MAX_ACCUMULATED_CHARS
            # Iterate in reverse so the most recently generated files are included first
            for name, code in reversed(list(accumulated_code.items())):
                entry = f"\n### {name}\n```{lang}\n{code}\n```"
                if budget - len(entry) < 0 and budget < _MAX_ACCUMULATED_CHARS:
                    # Budget exhausted; skip older files
                    prompt_parts.append(f"\n_(older generated files omitted for prompt size)_")
                    break
                prompt_parts.append(entry)
                budget -= len(entry)
            prompt_parts.append("")

        # ── 8. Completeness Rules ──
        prompt_parts.extend([
            "## COMPLETENESS RULES",
            "- Generate the COMPLETE file. NEVER stop mid-function or mid-class.",
            "- If the file needs 500+ lines, generate ALL of them. No shortcuts.",
            "- Every function must be fully implemented with real logic.",
            "- Every class must have all methods with complete bodies.",
            "- NEVER leave placeholder comments like 'add more here' or 'implement later'.",
            "",
        ])

        # ── 9. User Feedback ──
        if user_feedback:
            prompt_parts.extend([
                "## User Feedback (incorporate into generation)",
                user_feedback,
                "",
            ])

        # ── 10. GENERATE ──
        prompt_parts.extend([
            "## GENERATE",
            f"Output ONLY the {lang} code file. No markdown wrapping, no explanations.",
            f"Error comments use `{fw_config.error_comment_prefix}`.",
            "Match table/column names EXACTLY from the contract.",
        ])

        return "\n".join(prompt_parts)


# Register the agent
_shubham = Shubham()
register_agent(_shubham)

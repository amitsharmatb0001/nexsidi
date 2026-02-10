---
name: Python Modular Monolith (FastAPI + DDD)
description: Scalable Python backend architecture using Vertical Slices, Domain-Driven Design, and Protocol-based Dependency Injection.
tags: [python, fastapi, ddd, modular-monolith, backend]
---

# Python Modular Monolith (FastAPI + DDD) Template

## 1. Architectural Overview
Ideally suited for enterprise Python backends that need the simplicity of a monolith but the strict boundaries of microservices.
- **Paradigm**: Vertical Slice Architecture + Modular Monolith
- **Core Principle**: "Modules" (Bounded Contexts) communicate via **Protocol-Based Dependency Injection**, never direct imports.
- **Dependency Management**: `uv` or `poetry`.

## 2. Folder Structure
The structure enforces strict separation of `app` (wiring), `modules` (features), and `shared` (kernel).

```text
src/
├── app/                    # Composition Root (Bootstrapper)
│   ├── container.py        # DI Container (Wiring)
│   ├── main.py             # FastAPI App Factory
│   └── middlewares.py
├── modules/                # Vertical Slices (Bounded Contexts)
│   ├── ordering/
│   │   ├── init.py         # Public Facade
│   │   ├── domain/         # Pure Logic (No external deps)
│   │   │   ├── models.py
│   │   │   ├── ports.py    # Interfaces (Protocols)
│   │   │   └── services.py
│   │   ├── application/    # Use Cases
│   │   │   ├── commands.py
│   │   │   └── dtos.py
│   │   ├── infrastructure/ # Adapters (DB/API)
│   │   │   ├── database/   # ORM Models (SQLAlchemy)
│   │   │   └── repositories.py
│   │   └── presentation/   # FastAPI Routers
│   │       └── fast.py
│   └── auth/
└── shared/                 # Shared Kernel (Utilites, Base Classes)
    ├── domain/
    └── infra/
```

## 3. Implementation Rules

### Protocol-Based Dependency Injection
Modules must NOT import other modules directly. Use Protocols.

**Port (Ordering Module):**
```python
class IUserGateway(Protocol):
    async def is_user_active(self, user_id: UUID) -> bool: ...
```

**Implementation (Auth Module Facade):**
```python
class AuthModuleFacade:
    async def is_user_active(self, user_id: UUID) -> bool:
        return await self.service.get_user(user_id).is_active
```

**Wiring (src/app/container.py):**
```python
place_order_service = providers.Factory(
    PlaceOrderService,
    user_gateway=auth_module_facade # Injection
)
```

## 4. Data Isolation
- **Rule**: No Foreign Keys between modules.
- **Schema Separation**: Use PostgreSQL Schemas (`identity`, `sales`).
- **ORM**: Explicit `__table_args__ = {"schema": "sales"}`.

## 5. Migration Strategy (Alembic)
Use a single `alembic.ini` but import metadata from all modules in `env.py`.
```python
# alembic/env.py
from src.modules.auth.infra.database import models as auth_models
from src.modules.ordering.infra.database import models as order_models
target_metadata = Base.metadata
```

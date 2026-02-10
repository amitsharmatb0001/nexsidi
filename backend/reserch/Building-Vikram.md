# Building Vikram: the AI Chief Architect for NexSidi

**Vikram is an AI agent that transforms vague product ideas into structured architectural blueprints — folder trees, database schemas, API contracts, and infrastructure specs — without writing a single line of application code.** This report delivers the five foundational phases needed to build Vikram: a golden DDD-based folder architecture, an API-first contract workflow, production-ready infrastructure templates, event-driven patterns for scale, and the complete system prompt ready for `app/agents/vikram.py`. Every pattern here is production-tested at companies like Google, Netflix, and Uber, and optimized for AI code generation.

---

## Phase 1: The golden architecture built on Domain-Driven Design

The foundation of any enterprise FastAPI backend is a **Modular Monolith** — a single deployable unit where each business domain lives in its own self-contained module with strict boundaries. This structure prevents spaghetti code at scale and makes future microservice extraction trivial.

### The complete folder tree

```
project-root/
├── src/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app factory, router registration
│   ├── config.py                        # Settings via pydantic-settings
│   │
│   ├── shared/                          # ── Shared Kernel ──
│   │   ├── __init__.py
│   │   ├── base_entity.py               # Base dataclass with id, timestamps
│   │   ├── domain_event.py              # DomainEvent base class
│   │   ├── event_bus.py                 # EventBus Protocol (interface)
│   │   ├── exceptions.py               # DomainException, EntityNotFound
│   │   └── value_objects.py             # Money, Email, Address
│   │
│   ├── infrastructure/                  # ── Global Infrastructure ──
│   │   ├── __init__.py
│   │   ├── database.py                  # SQLAlchemy engine, session factory
│   │   ├── event_bus_impl.py            # InMemoryEventBus (swap for RabbitMQ later)
│   │   ├── middleware.py                # CORS, logging, request-id
│   │   └── redis.py                     # Redis connection pool
│   │
│   ├── auth/                            # ── Auth Bounded Context ──
│   │   ├── __init__.py                  # Public API: exports router, get_auth_service
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── entities.py              # User entity (pure dataclass)
│   │   │   ├── value_objects.py         # HashedPassword, Role
│   │   │   ├── events.py               # UserRegistered, UserDeactivated
│   │   │   ├── services.py             # AuthDomainService (password hashing, validation)
│   │   │   └── repository.py           # UserRepositoryProtocol (interface)
│   │   ├── application/
│   │   │   ├── __init__.py
│   │   │   ├── services.py             # AuthAppService (register, login use cases)
│   │   │   ├── commands.py             # RegisterUserCommand, LoginCommand
│   │   │   └── dtos.py                 # UserDTO (data out to API layer)
│   │   ├── infrastructure/
│   │   │   ├── __init__.py
│   │   │   ├── orm_models.py           # SQLAlchemy UserModel
│   │   │   ├── repository.py           # SQLAlchemyUserRepository
│   │   │   └── jwt_service.py          # JWT token creation/validation
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── routes.py               # FastAPI router (/auth/login, /auth/register)
│   │       ├── schemas.py              # Pydantic request/response models
│   │       └── dependencies.py         # Depends() wiring for this module
│   │
│   ├── orders/                          # ── Orders Bounded Context ──
│   │   ├── __init__.py
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── entities.py              # Order, OrderItem (aggregate root)
│   │   │   ├── value_objects.py         # OrderStatus, Money
│   │   │   ├── events.py               # OrderCreated, OrderPaid, OrderCancelled
│   │   │   ├── services.py             # OrderDomainService (pricing, validation)
│   │   │   └── repository.py           # OrderRepositoryProtocol
│   │   ├── application/
│   │   │   ├── __init__.py
│   │   │   ├── services.py             # OrderAppService (create_order use case)
│   │   │   ├── commands.py             # CreateOrderCommand
│   │   │   ├── ports.py                # UserVerifier Protocol (what Orders needs from Auth)
│   │   │   └── event_handlers.py       # Handles events from other modules
│   │   ├── infrastructure/
│   │   │   ├── __init__.py
│   │   │   ├── orm_models.py           # SQLAlchemy OrderModel, OrderItemModel
│   │   │   ├── repository.py           # SQLAlchemyOrderRepository
│   │   │   └── auth_adapter.py         # AuthUserVerifier (implements UserVerifier)
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── routes.py
│   │       ├── schemas.py
│   │       └── dependencies.py
│   │
│   └── inventory/                       # ── Inventory Bounded Context ──
│       ├── __init__.py
│       ├── domain/ ...                  # (same layered structure)
│       ├── application/ ...
│       ├── infrastructure/ ...
│       └── api/ ...
│
├── tests/
│   ├── unit/                            # Domain logic tests (no DB)
│   │   ├── test_order_entity.py
│   │   └── test_auth_service.py
│   ├── integration/                     # Repository tests (with test DB)
│   └── api/                             # HTTP endpoint tests
│
├── migrations/                          # Alembic
├── openapi/                             # OpenAPI specs (API-first)
├── infrastructure/                      # Terraform, K8s YAML, Dockerfiles
├── pyproject.toml
├── Dockerfile
└── .env.example
```

### Why each layer exists

The **domain layer** (`domain/`) contains pure Python dataclasses with zero framework imports — no SQLAlchemy, no Pydantic, no FastAPI. If you see `import sqlalchemy` in a domain file, the architecture is broken. This layer holds entities, value objects, domain events, and repository interfaces (Python Protocols). The **application layer** (`application/`) orchestrates use cases by calling domain objects through infrastructure ports. It defines commands, DTOs, and Protocols for external dependencies. The **infrastructure layer** (`infrastructure/`) provides concrete implementations — SQLAlchemy repositories, HTTP adapters, message broker publishers. The **API layer** (`api/`) is the thinnest possible HTTP translation layer: validate input → call service → format output. Routes should be ~5 lines of code maximum.

### Cross-module communication without circular dependencies

The critical pattern for preventing spaghetti: **modules never import each other's internals**. Instead, they depend on Protocol interfaces. Here is the complete working example:

```python
# src/orders/application/ports.py — Orders defines WHAT it needs (interface)
from typing import Protocol
from uuid import UUID

class UserVerifier(Protocol):
    """Orders module declares it needs to verify users exist.
    It does NOT know or care about Auth's implementation."""
    async def user_exists(self, user_id: UUID) -> bool: ...


# src/orders/application/services.py — Orders uses the interface
from uuid import UUID
from src.orders.domain.entities import Order
from src.orders.domain.repository import OrderRepositoryProtocol
from src.orders.application.ports import UserVerifier

class OrderAppService:
    def __init__(self, order_repo: OrderRepositoryProtocol, user_verifier: UserVerifier):
        self._order_repo = order_repo
        self._user_verifier = user_verifier

    async def create_order(self, user_id: UUID, items: list[dict]) -> Order:
        if not await self._user_verifier.user_exists(user_id):
            raise ValueError(f"User {user_id} not found")
        order = Order.create(user_id=user_id, items=items)
        await self._order_repo.save(order)
        return order


# src/orders/infrastructure/auth_adapter.py — Concrete implementation
from uuid import UUID
from src.auth.application.services import AuthAppService
from src.orders.application.ports import UserVerifier

class AuthUserVerifier(UserVerifier):
    def __init__(self, auth_service: AuthAppService):
        self._auth_service = auth_service

    async def user_exists(self, user_id: UUID) -> bool:
        user = await self._auth_service.get_user_by_id(user_id)
        return user is not None


# src/orders/api/dependencies.py — Wiring via FastAPI Depends
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_async_session
from src.orders.infrastructure.repository import SQLAlchemyOrderRepository
from src.orders.infrastructure.auth_adapter import AuthUserVerifier
from src.auth.application.services import AuthAppService

async def get_order_service(session: AsyncSession = Depends(get_async_session)):
    order_repo = SQLAlchemyOrderRepository(session)
    auth_service = AuthAppService(...)  # injected similarly
    user_verifier = AuthUserVerifier(auth_service)
    return OrderAppService(order_repo=order_repo, user_verifier=user_verifier)
```

**The dependency direction is always inward**: API → Application → Domain. Infrastructure implements Domain interfaces. Modules depend on each other's Protocols, never concrete classes. When extracting to microservices, swap `AuthUserVerifier` for `AuthHttpUserVerifier` that makes an HTTP call — **zero business logic changes**.

### Three anti-patterns that create spaghetti

**Cross-module direct database access** is the most destructive. When Module A queries Module B's tables directly (`from src.auth.infrastructure.orm_models import UserModel`), you couple their schemas permanently. Fix: always go through the module's public application service. **Fat API routes** embed business logic, database queries, and side effects in route handlers, making them untestable without a full HTTP + DB stack. Fix: routes should be 5 lines — parse, call service, return. **Circular module imports** happen when Auth imports from Orders and Orders imports from Auth. Fix: use domain events for one direction and Protocol interfaces for the other. Enforce boundaries with `import-linter` in CI:

```toml
# pyproject.toml
[tool.importlinter]
root_packages = ["src"]
[[tool.importlinter.contracts]]
name = "Domain layer independence"
type = "independence"
modules = ["src.auth.domain", "src.orders.domain", "src.inventory.domain"]
```

---

## Phase 2: API-first contracts generated before code

The API-first workflow ensures frontend and backend teams build against the same contract. **The OpenAPI specification becomes the single source of truth**, and all models are auto-generated from it.

### Complete OpenAPI 3.1 specification

```json
{
  "openapi": "3.1.0",
  "info": { "title": "NexSidi API", "version": "1.0.0" },
  "servers": [
    { "url": "https://api.nexsidi.com/v1", "description": "Production" },
    { "url": "http://localhost:8000/v1", "description": "Local" }
  ],
  "paths": {
    "/auth/login": {
      "post": {
        "operationId": "loginUser",
        "tags": ["Auth"],
        "summary": "Authenticate user and receive JWT",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": { "$ref": "#/components/schemas/LoginRequest" }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful authentication",
            "content": {
              "application/json": {
                "schema": { "$ref": "#/components/schemas/LoginResponse" }
              }
            }
          },
          "401": {
            "description": "Invalid credentials",
            "content": {
              "application/json": {
                "schema": { "$ref": "#/components/schemas/ErrorResponse" }
              }
            }
          }
        }
      }
    },
    "/orders": {
      "post": {
        "operationId": "createOrder",
        "tags": ["Orders"],
        "security": [{ "BearerAuth": [] }],
        "summary": "Create a new order",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": { "$ref": "#/components/schemas/CreateOrderRequest" }
            }
          }
        },
        "responses": {
          "201": {
            "description": "Order created",
            "content": {
              "application/json": {
                "schema": { "$ref": "#/components/schemas/CreateOrderResponse" }
              }
            }
          },
          "400": {
            "content": { "application/json": { "schema": { "$ref": "#/components/schemas/ErrorResponse" } } }
          },
          "401": {
            "content": { "application/json": { "schema": { "$ref": "#/components/schemas/ErrorResponse" } } }
          }
        }
      }
    }
  },
  "components": {
    "securitySchemes": {
      "BearerAuth": { "type": "http", "scheme": "bearer", "bearerFormat": "JWT" }
    },
    "schemas": {
      "LoginRequest": {
        "type": "object",
        "required": ["email", "password"],
        "properties": {
          "email": { "type": "string", "format": "email" },
          "password": { "type": "string", "format": "password", "minLength": 8 }
        }
      },
      "LoginResponse": {
        "type": "object",
        "required": ["access_token", "token_type", "expires_in"],
        "properties": {
          "access_token": { "type": "string" },
          "token_type": { "type": "string" },
          "expires_in": { "type": "integer" }
        }
      },
      "OrderItem": {
        "type": "object",
        "required": ["product_id", "quantity"],
        "properties": {
          "product_id": { "type": "string", "format": "uuid" },
          "quantity": { "type": "integer", "minimum": 1 }
        }
      },
      "CreateOrderRequest": {
        "type": "object",
        "required": ["items"],
        "properties": {
          "items": { "type": "array", "minItems": 1, "items": { "$ref": "#/components/schemas/OrderItem" } },
          "shipping_address": { "type": ["string", "null"] },
          "notes": { "type": ["string", "null"] }
        }
      },
      "CreateOrderResponse": {
        "type": "object",
        "required": ["id", "status", "total_amount", "created_at"],
        "properties": {
          "id": { "type": "string", "format": "uuid" },
          "status": { "type": "string", "enum": ["pending", "confirmed", "shipped", "delivered", "cancelled"] },
          "total_amount": { "type": "number" },
          "created_at": { "type": "string", "format": "date-time" }
        }
      },
      "ErrorResponse": {
        "type": "object",
        "required": ["error_code", "message"],
        "properties": {
          "error_code": { "type": "string" },
          "message": { "type": "string" },
          "details": {
            "type": ["array", "null"],
            "items": {
              "type": "object",
              "properties": { "field": { "type": "string" }, "reason": { "type": "string" } }
            }
          }
        }
      }
    }
  }
}
```

### Auto-generating Pydantic models and TypeScript interfaces

The `datamodel-code-generator` tool converts this spec directly into **Pydantic v2 models**:

```bash
pip install datamodel-code-generator

datamodel-codegen \
  --input openapi.json \
  --input-file-type openapi \
  --output-model-type pydantic_v2.BaseModel \
  --output src/generated/models.py \
  --snake-case-field \
  --strict-nullable \
  --target-python-version 3.12
```

This generates production-ready models like:

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    email: str = Field(..., description='User email address')
    password: str = Field(..., min_length=8)

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class OrderItem(BaseModel):
    product_id: UUID
    quantity: int = Field(..., ge=1)

class CreateOrderRequest(BaseModel):
    items: list[OrderItem] = Field(..., min_length=1)
    shipping_address: Optional[str] = None
    notes: Optional[str] = None

class Status(Enum):
    pending = 'pending'
    confirmed = 'confirmed'
    shipped = 'shipped'
    delivered = 'delivered'
    cancelled = 'cancelled'

class CreateOrderResponse(BaseModel):
    id: UUID
    status: Status
    total_amount: float
    created_at: datetime
```

For **TypeScript interfaces**, use `openapi-typescript`:

```bash
npm install -D openapi-typescript
npx openapi-typescript ./openapi.json -o ./src/api/schema.d.ts
```

This generates type-safe interfaces the frontend team can use immediately. The complete pipeline is: **User Story → Vikram → OpenAPI Spec → datamodel-codegen → Pydantic Models + TypeScript Types → Mock Server (Prism)**. Both teams work in parallel from day one.

### AI agent prompt template for API spec generation

Vikram uses this template to convert natural language into OpenAPI specs:

- **Paths**: lowercase kebab-case nouns (`/user-profiles`, `/order-items`)
- **Operation IDs**: camelCase verbs (`createOrder`, `listUsers`, `getUserById`)
- **Schema names**: PascalCase with pattern `{Action}{Resource}Request/Response`
- **Fields**: snake_case (`created_at`, `user_id`)
- **Status codes**: GET→200, POST→201, PUT→200, DELETE→204; always include 400, 401, 422
- **Pagination**: wrap list responses in `{ items, total, page, page_size, has_next }`
- **IDs**: `type: string, format: uuid`; Timestamps: `format: date-time`; Nullable: `type: ["string", "null"]`

---

## Phase 3: Production infrastructure templates

### Production Dockerfile with multi-stage build

```dockerfile
# Stage 1: Builder
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY app/ ./app/
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

The **multi-stage build** produces a final image **~200MB smaller** than a single-stage build by excluding build tools. The `--mount=type=cache` directive caches pip downloads across builds, cutting rebuild time from minutes to seconds. The **non-root user** prevents container escape attacks. Layer ordering (requirements before code) ensures dependency changes don't invalidate the application code cache.

### Kubernetes deployment with health probes and autoscaling

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fastapi-app
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0       # Zero-downtime deploys
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
      containers:
        - name: fastapi
          image: REGION-docker.pkg.dev/PROJECT/REPO/fastapi-app:latest
          resources:
            requests: { cpu: "100m", memory: "128Mi" }
            limits:   { cpu: "500m", memory: "512Mi" }
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 15
            periodSeconds: 20
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /ready, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          startupProbe:
            httpGet: { path: /health, port: 8000 }
            failureThreshold: 12
            periodSeconds: 5
          envFrom:
            - configMapRef: { name: fastapi-config }
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: { name: fastapi-secrets, key: DATABASE_URL }
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: fastapi-app-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: fastapi-app
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies: [{ type: Pods, value: 4, periodSeconds: 60 }]
    scaleDown:
      stabilizationWindowSeconds: 300
      policies: [{ type: Pods, value: 1, periodSeconds: 60 }]
```

The **`maxUnavailable: 0`** ensures zero-downtime deployments. The **startup probe** gives containers up to 60 seconds to initialize (12 × 5s) before liveness checks begin — critical for FastAPI apps that preload ML models or run migrations. The HPA's **asymmetric scaling behavior** scales up aggressively (4 pods/minute) but scales down conservatively (1 pod/minute) to handle traffic spikes without flapping.

### Terraform for Cloud SQL with private IP

```hcl
resource "google_compute_network" "main" {
  name                    = "main-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_global_address" "private_ip_range" {
  name          = "cloudsql-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

resource "google_sql_database_instance" "postgres" {
  name             = "fastapi-postgres"
  database_version = "POSTGRES_15"
  region           = var.region
  depends_on       = [google_service_networking_connection.private_vpc]

  settings {
    tier              = "db-custom-2-4096"
    availability_type = "REGIONAL"             # HA with automatic failover
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false                  # No public IP — private only
      private_network = google_compute_network.main.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
      }
    }
  }
}
```

For **Cloud Run** (the recommended starting point for NexSidi), use the `google_cloud_run_v2_service` resource with a VPC connector for Cloud SQL access, Secret Manager for credentials, and `min_instance_count = 1` to avoid cold starts. **Cloud Run is preferred over GKE** for stateless APIs — it offers zero ops overhead, automatic TLS, scale-to-zero, and pay-per-request pricing. Move to GKE only when you need sidecar containers, service mesh, or sub-100ms p99 latency guarantees.

---

## Phase 4: Event-driven architecture for 10,000+ concurrent operations

### Publisher/Consumer pattern with RabbitMQ

The core pattern: **fire-and-forget publishing** from FastAPI endpoints, with dedicated **consumer workers** processing events asynchronously.

```python
# Publisher — FastAPI endpoint (fire-and-forget)
class RabbitMQPublisher:
    def __init__(self, amqp_url: str):
        self.connection = None
        self.exchange = None

    async def connect(self):
        self.connection = await aio_pika.connect_robust(self.amqp_url)
        channel = await self.connection.channel()
        self.exchange = await channel.declare_exchange(
            "events", aio_pika.ExchangeType.TOPIC, durable=True
        )

    async def publish(self, routing_key: str, message: dict):
        await self.exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(uuid.uuid4()),
            ),
            routing_key=routing_key,
        )

@app.post("/orders")
async def create_order(request: CreateOrderRequest):
    order_id = str(uuid.uuid4())
    event = {"event_type": "ORDER_CREATED", "data": {"order_id": order_id, ...}}
    await publisher.publish("order.created", event)  # Non-blocking
    return {"order_id": order_id, "status": "accepted"}
```

```python
# Consumer — Standalone worker process
async def handle_order_created(message: aio_pika.abc.AbstractIncomingMessage):
    async with message.process():  # Auto-ack on success, nack on exception
        data = json.loads(message.body.decode())
        await send_confirmation_email(data["data"])
        await update_inventory(data["data"])

async def main():
    connection = await aio_pika.connect_robust("amqp://guest:guest@localhost/")
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    queue = await channel.declare_queue("order_notifications", durable=True)
    await queue.bind(exchange, routing_key="order.created")
    await queue.consume(handle_order_created)
```

### Dead Letter Queues with exponential backoff

Failed messages need structured retry logic. Create **separate retry queues per delay level** to avoid head-of-line blocking:

```python
RETRY_DELAYS = [1, 5, 15, 60, 300]  # seconds — exponential backoff
MAX_RETRIES = 5

async def process_with_retry(message):
    async with message.process(requeue=False):
        retry_count = (message.headers or {}).get("x-retry-count", 0)
        try:
            await process_order(json.loads(message.body))
        except TransientError:
            if retry_count < MAX_RETRIES:
                delay = RETRY_DELAYS[min(retry_count, len(RETRY_DELAYS) - 1)]
                headers = {"x-retry-count": retry_count + 1}
                await channel.default_exchange.publish(
                    aio_pika.Message(body=message.body, headers=headers),
                    routing_key=f"orders.retry.{delay}s"
                )
            else:
                raise  # Goes to DLQ via dead-letter exchange
        except PermanentError:
            raise  # Straight to DLQ — no retries for poison messages
```

### Library comparison and recommendations

| Library | Async | FastAPI Integration | Best For | Community |
|---------|-------|-------------------|----------|-----------|
| **FastStream** | ✅ Native | ✅ First-class RouterPlugin | New async projects, multi-broker | ~8K stars |
| **aio-pika** | ✅ Native | ✅ Via lifespan | Fine-grained AMQP control, DLX | ~3K stars |
| **Celery** | ⚠️ Limited | ⚠️ Separate process | Mature codebases, periodic tasks | ~25K stars |
| **Dramatiq** | ⚠️ Thread-based | ⚠️ Separate process | Simpler Celery alternative | ~4K stars |
| **GCP Pub/Sub** | ⚠️ Sync | ✅ Manual | GCP-native, auto-scaling | GCP ecosystem |

**Recommendation for NexSidi**: Start with **FastStream** for its Pydantic-native message validation, AsyncAPI documentation generation, and in-memory TestBroker for unit testing. Use **aio-pika** directly when you need advanced AMQP patterns like DLX routing or custom QoS tuning.

### Scaling patterns for high concurrency

**Prefetch tuning** is the single most impactful setting: `prefetch_count=10-50` for most workloads (never unlimited). **Connection pooling** via `aio_pika.pool.Pool` with max 10 connections and 50 channels prevents socket exhaustion. **Idempotency** is non-negotiable — use Redis `SET NX` with the message ID as key to deduplicate:

```python
class IdempotentConsumer:
    async def process_if_new(self, message_id: str, handler, data: dict):
        was_set = await self.redis.set(f"processed:{message_id}", "1", nx=True, ex=7*86400)
        if not was_set:
            return False  # Duplicate — skip
        try:
            await handler(data)
        except Exception:
            await self.redis.delete(f"processed:{message_id}")
            raise
```

For distributed transactions across bounded contexts, use the **Saga pattern** with an orchestrator that maintains compensating actions for each step (create order → reserve inventory → charge payment, with rollbacks for failures).

---

## Phase 5: The complete Vikram system prompt

This system prompt is ready to paste into `app/agents/vikram.py`. It encodes all architectural patterns from Phases 1–4 into Vikram's decision-making framework.

```python
VIKRAM_SYSTEM_PROMPT = """
# VIKRAM — The Chief Architect Agent
# NexSidi Autonomous Software Development Platform

## IDENTITY
You are Vikram, the Chief Architect of NexSidi. You transform vague product ideas
into precise, structured Architecture Blueprints in JSON format. You are the FIRST
agent invoked in any project — your blueprint feeds directly into Tilotma (Backend),
Saanvi (Frontend), Shubham (DevOps), Navya (QA), and Pranav (PM).

## CORE RULES — NON-NEGOTIABLE
1. You NEVER write application code (no function bodies, no business logic implementations).
2. You ONLY output Architecture Blueprints as structured JSON.
3. Every service follows Domain-Driven Design with Modular Monolith structure.
4. Every API endpoint is defined contract-first in OpenAPI 3.1 format.
5. All infrastructure uses containerized, cloud-native patterns.
6. Event-driven communication between bounded contexts is mandatory for async flows.

## INPUT
You receive a natural language product description from a user or from Pranav (PM Agent).
Examples: "Build a Food Delivery App", "Create an HR Management System", "Build a SaaS invoicing platform".

## OUTPUT FORMAT
You MUST output a single JSON object with this exact schema:

```json
{
  "project_name": "string — kebab-case project identifier",
  "description": "string — one-paragraph summary of the system",
  "architecture_style": "modular_monolith | microservices",
  "tech_stack": {
    "backend": "Python 3.12 + FastAPI",
    "frontend": "React | Vue | Next.js",
    "database": "PostgreSQL 15",
    "cache": "Redis 7",
    "message_broker": "RabbitMQ | Google Pub/Sub",
    "search": "Elasticsearch | Meilisearch | null",
    "storage": "Google Cloud Storage | S3 | null",
    "deployment": "GCP Cloud Run | GKE"
  },

  "services": [
    {
      "name": "string — kebab-case service name",
      "description": "string — what this service does",
      "bounded_context": "string — DDD bounded context name",
      "folder_structure": {
        "src/{module}/domain/entities.py": "Domain entities — pure dataclasses, no framework imports",
        "src/{module}/domain/value_objects.py": "Value objects — immutable, equality by value",
        "src/{module}/domain/events.py": "Domain events — OrderCreated, UserRegistered, etc.",
        "src/{module}/domain/services.py": "Domain services — business logic requiring multiple entities",
        "src/{module}/domain/repository.py": "Repository Protocol — interface only, no implementation",
        "src/{module}/application/services.py": "Application services — use case orchestration",
        "src/{module}/application/commands.py": "Command objects — CreateOrderCommand, etc.",
        "src/{module}/application/ports.py": "Port interfaces — what this module needs from others",
        "src/{module}/infrastructure/orm_models.py": "SQLAlchemy ORM models",
        "src/{module}/infrastructure/repository.py": "Concrete repository implementation",
        "src/{module}/infrastructure/adapters.py": "Adapters for external services and other modules",
        "src/{module}/api/routes.py": "FastAPI router — thin HTTP translation layer",
        "src/{module}/api/schemas.py": "Pydantic request/response schemas",
        "src/{module}/api/dependencies.py": "FastAPI Depends() wiring — composition root"
      },
      "dependencies_on_other_services": [
        {
          "service": "string — name of service this depends on",
          "communication": "sync_protocol | async_event",
          "interface": "string — Protocol class name (e.g., UserVerifier)",
          "description": "string — what data/capability is needed"
        }
      ]
    }
  ],

  "database_schema": {
    "tables": [
      {
        "name": "string — snake_case table name",
        "service": "string — which service owns this table",
        "columns": [
          {
            "name": "string",
            "type": "uuid | string | text | integer | decimal | boolean | timestamp | jsonb",
            "primary_key": "boolean",
            "nullable": "boolean",
            "default": "string | null",
            "description": "string"
          }
        ],
        "indexes": [
          {
            "columns": ["string"],
            "unique": "boolean",
            "name": "string"
          }
        ],
        "foreign_keys": [
          {
            "column": "string",
            "references_table": "string",
            "references_column": "string",
            "on_delete": "CASCADE | SET NULL | RESTRICT"
          }
        ]
      }
    ]
  },

  "api_endpoints": [
    {
      "service": "string — which service owns this endpoint",
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "string — e.g., /api/v1/orders",
      "operation_id": "string — camelCase, e.g., createOrder",
      "summary": "string",
      "auth_required": "boolean",
      "request_body": {
        "schema_name": "string — PascalCase, e.g., CreateOrderRequest",
        "fields": [
          {
            "name": "string — snake_case",
            "type": "string | integer | number | boolean | array | object | uuid",
            "required": "boolean",
            "description": "string",
            "validation": "string | null — e.g., min_length=8, ge=1"
          }
        ]
      },
      "response": {
        "status_code": "integer",
        "schema_name": "string",
        "fields": [
          {
            "name": "string",
            "type": "string",
            "description": "string"
          }
        ]
      },
      "error_responses": [400, 401, 404, 422]
    }
  ],

  "events": [
    {
      "name": "string — UPPER_SNAKE_CASE, e.g., ORDER_CREATED",
      "publisher_service": "string",
      "consumer_services": ["string"],
      "routing_key": "string — dot notation, e.g., order.created",
      "payload": {
        "fields": [
          {
            "name": "string",
            "type": "string",
            "description": "string"
          }
        ]
      },
      "triggers": "string — what action causes this event",
      "consumer_actions": ["string — what each consumer does with this event"]
    }
  ],

  "infrastructure": {
    "databases": [
      {
        "type": "PostgreSQL | Redis | Elasticsearch",
        "purpose": "string",
        "config": {
          "version": "string",
          "high_availability": "boolean",
          "estimated_storage_gb": "integer"
        }
      }
    ],
    "message_brokers": [
      {
        "type": "RabbitMQ | Google Pub/Sub",
        "purpose": "string",
        "queues": ["string — queue names"],
        "exchanges": [
          {
            "name": "string",
            "type": "topic | direct | fanout"
          }
        ]
      }
    ],
    "external_services": [
      {
        "name": "string — e.g., Stripe, SendGrid, Twilio",
        "purpose": "string",
        "integration_pattern": "REST API | Webhook | SDK"
      }
    ],
    "deployment": {
      "platform": "GCP Cloud Run | GKE",
      "container_registry": "Google Artifact Registry",
      "ci_cd": "GitHub Actions | Cloud Build",
      "monitoring": "Google Cloud Monitoring + Logging",
      "secrets_management": "Google Secret Manager"
    }
  },

  "cross_cutting_concerns": {
    "authentication": "JWT Bearer tokens via /auth/login",
    "authorization": "Role-based (RBAC) with roles stored in users table",
    "rate_limiting": "Redis-based, per-user token bucket",
    "logging": "Structured JSON logs via structlog",
    "error_handling": "Standardized ErrorResponse schema across all services",
    "pagination": "Cursor-based for lists, with page/page_size query params",
    "api_versioning": "URL path versioning (/v1/, /v2/)"
  }
}
```

## ARCHITECTURAL DECISION RULES

### When to create separate services (bounded contexts):
- Each major business domain gets its own module: auth, orders, payments, notifications, etc.
- A module should own its own data — no cross-module foreign keys in database schema.
- If two concepts change for different business reasons, they belong in different modules.
- Start with modular monolith (single deployable) unless the user explicitly requests microservices.

### Database schema rules:
- Every table has: `id` (UUID, PK), `created_at` (timestamp), `updated_at` (timestamp, nullable).
- Use UUIDs for all primary keys, never auto-increment integers.
- Foreign keys ONLY within the same bounded context. Cross-context references use UUID columns without FK constraints.
- Include appropriate indexes for all foreign key columns and commonly queried fields.
- Use `jsonb` columns for flexible/semi-structured data, not for core business fields.

### API endpoint rules:
- RESTful resource naming: plural nouns, kebab-case (`/api/v1/order-items`).
- Standard methods: GET (list/retrieve), POST (create), PUT/PATCH (update), DELETE (remove).
- GET list → 200; POST create → 201; PUT/PATCH update → 200; DELETE → 204.
- All endpoints include error responses: 400 (bad request), 401 (unauthorized), 422 (validation).
- Protected endpoints require BearerAuth; only login/register are public.
- List endpoints support pagination: `page`, `page_size` query params, response wraps in `{items, total, page, page_size, has_next}`.

### Event rules:
- Events are the ONLY way bounded contexts communicate asynchronously.
- Event names: UPPER_SNAKE_CASE (ORDER_CREATED, PAYMENT_COMPLETED).
- Routing keys: dot-separated lowercase (order.created, payment.completed).
- Every state-changing operation that other modules care about MUST publish an event.
- Consumer actions must be idempotent — processing the same event twice produces the same result.

### Infrastructure rules:
- PostgreSQL for primary storage, Redis for caching and rate limiting.
- RabbitMQ for event-driven communication (or Google Pub/Sub if GCP-native).
- All services containerized with multi-stage Docker builds.
- Deploy to GCP Cloud Run (default) or GKE (if complex orchestration needed).
- Secrets via Google Secret Manager, never in environment variables or code.

## ANALYSIS PROCESS

When you receive a product description, follow this process:

1. **Identify Bounded Contexts**: Break the product into distinct business domains.
   Ask: "What are the major nouns/concepts? Who are the actors? What are the core workflows?"

2. **Define Domain Entities**: For each context, identify the aggregate roots, entities, and value objects.
   Ask: "What data does this domain own? What are the invariants/business rules?"

3. **Map Relationships**: Determine how contexts interact.
   Ask: "Does Context A need data from Context B? Is it sync (API call) or async (event)?"

4. **Design API Surface**: Define CRUD + custom endpoints for each context.
   Ask: "What actions can users perform? What data flows in and out?"

5. **Define Events**: Identify state changes that other contexts need to know about.
   Ask: "When X happens in Context A, who else needs to react?"

6. **Schema Design**: Create tables with proper normalization, indexes, and constraints.
   Ask: "What queries will be frequent? What needs to be indexed?"

7. **Infrastructure Assessment**: Determine what infrastructure components are needed.
   Ask: "Do we need real-time features (WebSocket)? File storage? Full-text search? Background jobs?"

## EXAMPLE

**Input**: "Build a Food Delivery App"

**Your Analysis** (internal, not output):
- Bounded Contexts: Auth, Restaurants, Menu, Orders, Delivery, Payments, Notifications
- Core Flow: User browses restaurants → adds items to cart → places order → payment processed → restaurant notified → driver assigned → delivery tracked → order completed
- Events: ORDER_PLACED → triggers payment; PAYMENT_COMPLETED → triggers restaurant notification; ORDER_ACCEPTED → triggers driver assignment; DELIVERY_COMPLETED → triggers rating prompt

**Your Output**: The complete JSON blueprint following the schema above, with all services, tables, endpoints, events, and infrastructure fully specified.

## REMEMBER
- You are an ARCHITECT, not a developer. Output blueprints, not code.
- Be specific: exact table names, exact endpoint paths, exact event names.
- Be complete: every service needs all layers (domain, application, infrastructure, api).
- Be consistent: naming conventions must be uniform across the entire blueprint.
- Think about scale: design for 10,000+ concurrent users from day one.
- Think about team autonomy: each bounded context should be independently developable and deployable.
"""
```

### How Vikram integrates with the NexSidi agent pipeline

Vikram produces the JSON blueprint, which flows directly to the other four agents. **Tilotma** receives the `folder_structure`, `database_schema`, and `api_endpoints` to generate the FastAPI backend with proper DDD layering. **Saanvi** receives the `api_endpoints` to generate the OpenAPI spec, then runs `openapi-typescript` to create type-safe frontend interfaces. **Shubham** receives the `infrastructure` block to generate Dockerfiles, Kubernetes manifests, and Terraform configs. **Navya** receives the `api_endpoints` and `events` to generate test cases for every endpoint and event handler.

The architecture is deliberately **non-recursive**: Vikram outputs structure, never functions. Other agents fill in the implementation. This separation prevents the common failure mode of AI code generation — writing code without understanding where it fits in the larger system.

---

## Conclusion

The architecture described here solves three problems that kill AI-generated codebases. **Structural chaos** is prevented by DDD's strict layer separation — domain logic never leaks into API routes, and modules communicate through Protocol interfaces, not direct imports. **Contract drift** is eliminated by the API-first workflow — OpenAPI specs generate both Python and TypeScript types from a single source of truth, making it impossible for frontend and backend to disagree. **Scaling bottlenecks** are avoided by event-driven patterns — bounded contexts communicate through RabbitMQ events with idempotent consumers, DLQ retry logic, and connection pooling tuned for **10,000+ concurrent operations**.

Vikram's key insight is constraint: by outputting only architecture blueprints (never application code), it forces every downstream agent to work within a coherent structural framework. The modular monolith pattern gives NexSidi a pragmatic starting point — a single deployable unit today that can be split into independent microservices tomorrow by swapping in-process adapters for HTTP clients and in-memory event buses for RabbitMQ publishers. No business logic changes required.
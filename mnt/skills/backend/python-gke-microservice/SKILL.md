---
name: Python Microservice (GKE + uv)
description: Production-ready container and deployment template for high-scale Python services on GKE.
tags: [python, gke, docker, kubernetes, uv]
---

# Python Microservice (GKE + uv) Template

## 1. Architectural Overview
Standardized container/deployment strategy for Staff-level engineering.
- **Package Manager**: `uv` (Rust-based, ultra-fast)
- **Container**: `python:3.12-slim-bookworm` (Debian-based for glibc)
- **Security**: Non-Root (UID 1001), Read-Only Filesystem
- **Concurrency**: Gunicorn (Process Manager) + Uvicorn (Worker)

## 2. Docker Strategy (Universal Template)
Optimized for caching and security.

```dockerfile
# Stage 1: Builder
FROM python:3.12-slim-bookworm AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.5.0 /uv /bin/uv

# Dependency Layer (Cached)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# App Layer
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd -g 1001 appgroup && \
    useradd -r -u 1001 -g appgroup appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appgroup /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER 1001
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "src.main:app"]
```

## 3. Kubernetes Deployment (GKE)
Key Configurations:
- **Probes**: Startup, Readiness, Liveness (The Triad).
- **QoS**: Guaranteed (Requests == Limits).
- **Security Context**: `readOnlyRootFilesystem: true`, `runAsNonRoot: true`.

```yaml
readinessProbe:
  httpGet: { path: /ready, port: 8000 }
  failureThreshold: 2
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  initialDelaySeconds: 5
```

## 4. Infrastructure (Terraform)
- **Cloud SQL**: Private IP only (VPC Peering).
- **Identity**: Workload Identity Federation (KSA -> GSA).

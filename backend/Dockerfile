# ── Stage 1: build ────────────────────────────────────────────────────────────
# Install C-extension build tools (gcc, libpq-dev) and compile all wheels.
# This stage is discarded in the final image so build tools never reach prod.
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install PRODUCTION dependencies only — no pytest/mypy/black/etc.
# The [dev] extras are intentionally excluded to minimise attack surface
# and image size.
RUN pip install --no-cache-dir --prefix=/install -e "."

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# Lean production image: only the installed packages, no build tools,
# no root process.
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install only the runtime shared-library (libpq) needed by psycopg2 —
# no compiler toolchain.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Copy compiled packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# ── Non-root user ─────────────────────────────────────────────────────────────
# Running as root inside a container is unnecessary and violates the
# principle of least privilege.  An appuser with no home directory and
# no shell minimises the blast radius of any container escape.
RUN useradd --no-create-home --shell /bin/false appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ── Health check ──────────────────────────────────────────────────────────────
# Docker (and Kubernetes liveness probes) use this to decide whether the
# container is healthy.  Uses the stdlib urllib so no extra dependency is
# needed.  start_period gives Uvicorn time to start before the first check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:8000/health', timeout=4); \
         sys.exit(0 if r.status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Multi-stage Docker build for the FastAPI backend.
# Targets Railway / Fly.io. Optimised for small layer count + fast cold start.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_HTTP_TIMEOUT=300 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy lockfile + project for dependency install
COPY pyproject.toml ./
COPY uv.lock* ./

# Install deps without the project itself (faster rebuilds when only code changes)
RUN uv sync --frozen --no-install-project --no-dev 2>/dev/null --group whatsapp --group memory || uv sync --no-install-project --no-dev --group whatsapp --group memory

# Copy source
COPY . .

# Install the project (no-op if pyproject was already installed)
RUN uv sync --frozen --no-dev 2>/dev/null --group whatsapp --group memory || uv sync --no-dev --group whatsapp --group memory

# Railway sets PORT; default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

# Healthcheck — Railway uses this to know when to route traffic
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

# Run as non-root for security
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/bash app
RUN chown -R app:app /app
USER app

CMD ["sh", "-c", "uv run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

# ── Builder: install dependencies (needs git for git+ deps) ───────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.6.0 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project --frozen

# ── Runtime: minimal image without git/perl/build tools ───────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 1001 --system appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser

WORKDIR /app

COPY --from=builder /app/.venv ./.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --chown=appuser:appgroup crunch_node ./crunch_node

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -sf http://localhost:8000/healthz || exit 1

CMD ["python", "-m", "crunch_node"]

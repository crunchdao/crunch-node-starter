FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 1001 --system appgroup && \
    adduser --system --uid 1001 --ingroup appgroup appuser

WORKDIR /app

# Crunch engine (from PyPI) — always install the absolute latest version.
# pip --upgrade + --no-cache-dir ensures Docker never serves a stale wheel.
# The CACHEBUST arg changes on every build (set to timestamp by docker-compose)
# which invalidates Docker layer cache and forces a fresh pip install.
ARG CACHEBUST=0
RUN pip install --no-cache-dir --upgrade crunch-node

# model-runner-client: force-reinstall from git master to pick up gateway_credentials
# support that is missing from the PyPI 0.11.0 wheel.
# TODO: remove once model-runner-client >= 0.12.0 is published to PyPI
RUN pip install --no-cache-dir --force-reinstall --no-deps \
    "model-runner-client @ git+https://github.com/crunchdao/model-runner-client.git@master"

# Challenge package (regular install, not editable)
COPY --chown=appuser:appgroup challenge ./challenge
RUN pip install --no-cache-dir ./challenge

# Operator config (CrunchConfig override, prediction schedules, callables)
COPY --chown=appuser:appgroup node/config ./config

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -sf http://localhost:8000/healthz || exit 1

CMD ["python", "-m", "crunch_node"]

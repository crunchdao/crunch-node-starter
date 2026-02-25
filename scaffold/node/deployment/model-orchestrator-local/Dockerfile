# Wraps the upstream model-orchestrator, fixing the certificates issue at build time.
# The upstream model-runner Dockerfile has `COPY certificates /workspace/certificates`
# but local dev builds never have a certificates directory.
#
# NOTE: This container requires Docker socket access and runs as root intentionally
# to manage sibling containers via the Docker API.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "git+https://github.com/crunchdao/model-orchestrator.git"

# Fix 1: replace COPY certificates with mkdir so local builds don't fail
RUN DOCKERFILE=$(python -c "import model_orchestrator.infrastructure; import os; print(os.path.join(os.path.dirname(model_orchestrator.infrastructure.__file__), 'Dockerfile'))") && \
    sed -i 's|^COPY certificates /workspace/certificates|RUN mkdir -p /workspace/certificates|' "$DOCKERFILE" && \
    grep -q 'mkdir -p /workspace/certificates' "$DOCKERFILE" || { echo "ERROR: sed patch did not apply"; exit 1; }

# Fix 2: add com.docker.compose.project label to model containers so they
# group with node services in Docker Desktop.  Reads DOCKER_COMPOSE_PROJECT
# env var at runtime; no-op when the var is unset.
RUN RUNNER=$(python -c "import model_orchestrator.infrastructure.local._runner; import inspect; print(inspect.getfile(model_orchestrator.infrastructure.local._runner))") && \
    sed -i '/network=network_name,/a\            labels={k: v for k, v in {"com.docker.compose.project": __import__("os").environ.get("DOCKER_COMPOSE_PROJECT", ""), "com.docker.compose.service": "model"}.items() if v} or None,' "$RUNNER"

ENTRYPOINT ["model-orchestrator"]

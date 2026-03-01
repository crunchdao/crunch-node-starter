#!/bin/sh
set -e

# Auto-discover challenge examples and operator submissions, create
# standalone orchestrator submission directories, and generate
# data/models.dev.yml.  Runs on every container start so code changes
# are picked up immediately.
python /app/config/sync_examples.py

# Expand ${CRUNCH_ID} in orchestrator config so the crunch id matches
# the one used by models.dev.yml and the rest of the stack.
# Write to /app/data/ (mutable volume) to avoid mutating the bind-mounted source.
if [ -n "$CRUNCH_ID" ]; then
    if command -v envsubst >/dev/null 2>&1; then
        envsubst '$CRUNCH_ID' < /app/config/orchestrator.dev.yml > /app/data/orchestrator.dev.yml
    else
        sed "s/\${CRUNCH_ID}/$CRUNCH_ID/g" /app/config/orchestrator.dev.yml > /app/data/orchestrator.dev.yml
    fi
fi

exec "$@"

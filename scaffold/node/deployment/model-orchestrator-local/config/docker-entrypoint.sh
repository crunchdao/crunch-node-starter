#!/bin/sh
set -e

# Auto-discover challenge examples and operator submissions, create
# standalone orchestrator submission directories, and generate
# data/models.dev.yml.  Runs on every container start so code changes
# are picked up immediately.
python /app/config/sync_examples.py

exec "$@"

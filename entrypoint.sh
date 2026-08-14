#!/bin/sh
set -e

echo "=== Aelira API Entrypoint ==="

# Run database migrations (idempotent — safe to run on every deploy)
echo "Running database migrations..."
# Fail closed: an API running against an incompatible schema is worse than
# a container that restarts. set -e terminates the container on failure.
alembic upgrade head
echo "Migrations complete."

# Start the API server.
# Default to 1 worker: the cloud job processor releases its FOR UPDATE
# SKIP LOCKED batch locks on the first commit (job_processor.py), so >1 worker
# can double-process jobs; sync Playwright in BackgroundTasks also deadlocks
# across workers (see Dockerfile). Override UVICORN_WORKERS only after
# the job-claim race is fixed.
echo "Starting uvicorn (workers=${UVICORN_WORKERS:-1})..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-1}"

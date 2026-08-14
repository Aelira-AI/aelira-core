#!/bin/sh
set -e

echo "=== Aelira API Entrypoint ==="

# Run database migrations (idempotent — safe to run on every deploy)
echo "Running database migrations..."
alembic upgrade head 2>&1 || {
    echo "WARNING: Alembic migration failed. API will start anyway."
    echo "Check migration logs and run manually if needed."
}
echo "Migrations complete."

# Start the API server.
# Default to 1 worker: the cloud job processor releases its FOR UPDATE
# SKIP LOCKED batch locks on the first commit (job_processor.py), so >1 worker
# can double-process jobs; sync Playwright in BackgroundTasks also deadlocks
# across workers (see Dockerfile). Override UVICORN_WORKERS only after
# the job-claim race is fixed.
echo "Starting uvicorn (workers=${UVICORN_WORKERS:-1})..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-1}"

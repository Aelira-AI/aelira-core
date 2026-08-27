#!/bin/sh
set -e

echo "=== Aelira API Entrypoint ==="

# Run database migrations (idempotent — safe to run on every deploy)
if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
  echo "Running database migrations..."
  # Fail closed: an API running against an incompatible schema is worse than
  # a container that restarts. set -e terminates the container on failure.
  alembic upgrade head
  echo "Migrations complete."
else
  echo "Migrations already completed by the API service."
fi

# A compose service may provide a dedicated command (notably the durable job
# worker). Workers wait for the healthy API/migration owner before starting.
if [ "$#" -gt 0 ]; then
  echo "Starting dedicated service: $*"
  exec "$@"
fi

# Queue execution belongs to the worker service; the API intentionally does
# not start a JobProcessor lifecycle.
echo "Starting uvicorn (workers=${UVICORN_WORKERS:-2})..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}" --no-proxy-headers

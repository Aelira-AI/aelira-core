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

# Start the API server
echo "Starting uvicorn..."
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 3

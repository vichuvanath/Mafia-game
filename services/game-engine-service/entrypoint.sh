#!/usr/bin/env sh
set -e

export PYTHONPATH="${PYTHONPATH}:."

echo "Running database migrations..."
alembic upgrade head || {
    echo "Migration failed or database not ready yet. Retrying in 2 seconds..."
    sleep 2
    alembic upgrade head
}

echo "Starting Game Engine Service..."
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"

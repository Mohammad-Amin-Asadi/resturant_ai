#!/bin/bash
set -e

echo "=== Django Backend Entrypoint ==="

# Wait for PostgreSQL to be ready
echo "Waiting for database to be ready..."
max_attempts=30
attempt=0
until python manage.py shell -c "
from django.db import connection
try:
    connection.ensure_connection()
    print('Database is ready!')
except Exception as e:
    print(f'Database is unavailable: {e}')
    exit(1)
" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        echo "Database connection failed after $max_attempts attempts"
        exit 1
    fi
    echo "Database is unavailable - sleeping (attempt $attempt/$max_attempts)"
    sleep 2
done

echo "Database is ready!"

# Run migrations
echo "Running migrations..."
python manage.py makemigrations --noinput || echo "No new migrations to create"
python manage.py migrate --noinput

echo "Migrations completed!"

# Collect static files (for production)
if [ "$DEBUG" != "True" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput || echo "Static files collection skipped"
fi

# Execute the command (gunicorn)
echo "Starting server..."
exec "$@"

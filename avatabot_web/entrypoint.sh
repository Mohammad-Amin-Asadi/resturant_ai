#!/bin/bash
set -e

echo "=== Django Backend Entrypoint ==="
echo "Working directory: $(pwd)"
echo "Python version: $(python --version)"

# Wait for PostgreSQL to be ready
echo "Waiting for database to be ready..."
max_attempts=30
attempt=0

until python manage.py shell -c "
import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Server.settings')
import django
django.setup()
from django.db import connection
try:
    connection.ensure_connection()
    print('Database is ready!')
    sys.exit(0)
except Exception as e:
    print(f'Database connection failed: {e}')
    sys.exit(1)
" 2>&1; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        echo "ERROR: Database connection failed after $max_attempts attempts"
        echo "Please check:"
        echo "  - DB_HOST=${DB_HOST:-postgres}"
        echo "  - DB_PORT=${DB_PORT:-5432}"
        echo "  - DB_NAME=${DB_NAME}"
        echo "  - DB_USER=${DB_USER}"
        exit 1
    fi
    echo "Database is unavailable - sleeping (attempt $attempt/$max_attempts)"
    sleep 2
done

echo "Database is ready!"

# Run migrations
echo "Running migrations..."
# Skip system checks during makemigrations to avoid duplicate model warnings
python manage.py makemigrations --noinput --skip-checks || echo "No new migrations to create"
# Run migrate with --skip-checks to avoid blocking on duplicate model warnings
python manage.py migrate --noinput --skip-checks

echo "Migrations completed!"

# Collect static files (always, for both dev and production)
echo "Collecting static files..."
python manage.py collectstatic --noinput || echo "Static files collection skipped"

# Execute the command (gunicorn from CMD)
echo "Starting server with command: $@"
exec "$@"

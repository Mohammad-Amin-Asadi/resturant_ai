#!/bin/sh

set -e

echo "Waiting for database to be ready..."
# Wait for PostgreSQL to be ready using Django's database connection
until python manage.py shell -c "
from django.db import connection
try:
    connection.ensure_connection()
    print('Database is ready!')
except Exception as e:
    print('Database is unavailable')
    exit(1)
" 2>/dev/null; do
    echo "Database is unavailable - sleeping"
    sleep 2
done

echo "Making migrations..."
python manage.py makemigrations --noinput || echo "No new migrations to create"

echo "Running migrations..."
python manage.py migrate --noinput

echo "Migrations completed successfully!"

# Execute the main command (runserver or whatever is passed)
exec "$@"


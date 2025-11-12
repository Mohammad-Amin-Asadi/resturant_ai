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

echo "Starting Django servers..."
echo "  - Restaurant service on port 5000 (mapped to 8000)"
echo "  - Taxi service on port 5001 (mapped to 8001)"

# Start restaurant service on port 5000 in background
SERVER_TYPE=restaurant python manage.py runserver 0.0.0.0:5000 &
RESTAURANT_PID=$!

# Start taxi service on port 5001 in background  
SERVER_TYPE=taxi python manage.py runserver 0.0.0.0:5001 &
TAXI_PID=$!

# Function to cleanup on exit
cleanup() {
    echo "Shutting down servers..."
    kill $RESTAURANT_PID 2>/dev/null || true
    kill $TAXI_PID 2>/dev/null || true
    wait
    exit 0
}

# Trap signals
trap cleanup SIGTERM SIGINT

# Wait for both processes
wait $RESTAURANT_PID $TAXI_PID


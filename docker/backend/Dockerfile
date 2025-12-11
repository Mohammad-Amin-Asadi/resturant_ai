FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install gunicorn

# Copy project files
COPY . /app/

# Create directories for static and media files
RUN mkdir -p /app/staticfiles /app/media

# Make entrypoint script executable
RUN chmod +x /app/entrypoint.sh || true

# Collect static files (will be done in entrypoint if needed)
# RUN python manage.py collectstatic --noinput || true

# Expose port
EXPOSE 8000

# Use entrypoint script for migrations, then start gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "Server.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]

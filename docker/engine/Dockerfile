FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy project files
COPY . /app/

# Create config directory if it doesn't exist
RUN mkdir -p /app/config/did /app/logs

# Expose ports (if needed for OpenSIPS communication)
# Note: OpenSIPS typically uses host network mode, so ports may not be needed here

# Set working directory to src for main.py
WORKDIR /app/src

# Start the engine
CMD ["python3", "-u", "main.py"]

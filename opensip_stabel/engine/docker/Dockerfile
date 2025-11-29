FROM python:3.10-slim-buster

WORKDIR /app

COPY . /app/

RUN pip install --no-cache-dir -r requirements.txt

# Use port 0 (random) to avoid port conflicts
# The actual port will be assigned by the OS
ENV EVENT_PORT=0
CMD ["python3", "-u", "src/init_db.py"]
CMD ["python3", "-u", "src/main.py"]

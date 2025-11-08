FROM python:3.11.4-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PATH="/PY/BIN/$PATH"

COPY ./requirements.txt /app/requirements.txt
COPY . /app/

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Make entrypoint script executable
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

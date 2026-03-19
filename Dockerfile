# ---------- Build stage ----------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY website/requirements.txt website/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt \
    -r website/requirements.txt \
    python-dotenv gunicorn

# ---------- Runtime stage ----------
FROM python:3.11-slim

WORKDIR /app

# Runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libglx-mesa0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ src/
COPY website/ website/
COPY config/ config/
COPY run_website.py .

# Create upload directory
RUN mkdir -p uploads

EXPOSE 8080

# Fly.io uses PORT env var; gunicorn for production
ENV WEB_CONCURRENCY=1

CMD ["sh", "-c", "gunicorn website.api.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080 --workers ${WEB_CONCURRENCY:-1} --timeout 120 --preload"]

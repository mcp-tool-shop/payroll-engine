# Demo Dockerfile
# Serves both the API and static UI

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY pyproject.toml .
COPY src/ src/

# Install the package and demo dependencies
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir \
        fastapi \
        uvicorn[standard] \
        databases[postgresql] \
        psycopg2-binary \
        asyncpg

# Copy demo files
COPY demo/ demo/
COPY migrations/ migrations/

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Run the API server
# Static files served from /app/demo/ui via fly.toml [[statics]]
CMD ["uvicorn", "demo.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

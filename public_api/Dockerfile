# Read-only public API. Build from the repository root:
#   docker build -f public_api/Dockerfile -t pta-public-api .
#
# The snapshot (docs/data, committed JSON) is baked into the image: no
# database, no secrets, no model client. Only the web framework is installed.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SNAPSHOT_DIR=/app/data

WORKDIR /app

COPY public_api/requirements.txt public_api/requirements.txt
RUN pip install --no-cache-dir -r public_api/requirements.txt

COPY public_api/main.py public_api/__init__.py public_api/
COPY docs/data /app/data

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "uvicorn public_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

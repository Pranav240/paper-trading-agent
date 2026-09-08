# Control API image (Phase 05).
#
# python:3.13-slim matches the version the project is developed against
# (papertrading/pyvenv.cfg says 3.13.2). Slim rather than alpine because
# requirements.txt pins psycopg-binary and numpy/pandas wheels, which are
# built against glibc — alpine's musl would force a source build of all
# three and turn a 30-second image build into a multi-minute one for no
# benefit.
FROM python:3.13-slim

# Bytecode caching is pointless in a container that starts once, and
# unbuffered stdout is what makes `docker compose logs` show uvicorn's
# output as it happens rather than in bursts when a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Requirements copied and installed on their own layer, before the source
# is copied in, so editing a .py file doesn't invalidate the pip install
# layer — the slowest step in the build by a wide margin.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as a non-root user: nothing in here needs root, and an image that
# doesn't need it shouldn't have it.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# No migrations here — the image shouldn't assume a database exists at
# build time or that it's the only thing talking to one. docker-compose
# runs db/bootstrap.py as part of the api service's start command, where
# it can be ordered after the postgres healthcheck.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

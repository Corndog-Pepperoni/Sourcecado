# The hosted demo backend: one container on Railway.
# Build context is this backend/ directory: `docker build backend`.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.lock ./
RUN python -m pip install --require-hashes -r requirements.lock

COPY coworker ./coworker

# Runtime state (secrets.json, mcp.json, run markers) goes under
# CLUB_STATE_DIR. On Railway, mount the persistent volume at this path.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/club \
    && chown -R app:app /data/club
ENV CLUB_STATE_DIR=/data/club
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec python -m coworker.run --host 0.0.0.0 --port ${PORT:-8080}"]

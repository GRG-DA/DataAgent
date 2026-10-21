# syntax=docker/dockerfile:1
#
# DataAgent billing server container.
# Build from the REPO ROOT (the context must also include keygen_app/):
#
#   docker build -f billing_server/Dockerfile -t dataagent-billing .
#
# Runtime state is never baked into the image: billing.sqlite3, .env and the
# Ed25519 private key are excluded via Dockerfile.dockerignore — provide them
# at runtime (env vars / a mounted volume).
#
# x86_64 only: libsql==0.1.11 ships no aarch64 wheels (see requirements.txt).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY billing_server/requirements.txt /app/billing_server/requirements.txt
RUN pip install --no-cache-dir -r /app/billing_server/requirements.txt \
 && useradd --create-home --uid 10001 billing

COPY billing_server /app/billing_server
COPY keygen_app /app/keygen_app

USER billing

EXPOSE 8090

# /healthz reports which components are configured (billing_server/main.py).
# Heroku ignores Docker HEALTHCHECK (it has its own checks) — harmless there.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4)"]

# One worker on purpose: the SQLite fallback store and the in-process rate
# limits assume a single process. Scale up, not out (or set TURSO_* so state
# is remote). ${PORT:-8090} lets the same image run on Heroku (injects PORT).
CMD ["sh", "-c", "exec uvicorn billing_server.main:app --host 0.0.0.0 --port ${PORT:-8090} --workers 1"]

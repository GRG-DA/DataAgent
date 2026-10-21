# ============================================================
# DataAgent — production image
# ============================================================
# CANONICAL RELEASE PYTHON VERSION: 3.13
#
# One version for every build path. The Windows desktop .exe, this image and
# the CI workflows must all build on the same interpreter, because
# requirements.txt gates a dependency on the interpreter version
# (`fastparquet>=2024.5.0 ; python_version < "3.13"`): a 3.11 build installs a
# DIFFERENT dependency set than a 3.13 build, so the artefact built locally
# would not be the artefact CI tested.
#
# Parquet on 3.13 uses pyarrow (an unconditional dependency) - pandas picks it
# automatically because no call site passes `engine=`.
#
# Keep this in sync with:
#   .github/workflows/build-windows.yml   (python-version)
#   .github/workflows/pytest.yml          (matrix python-version)
# ============================================================
FROM python:3.13-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ============================================================
# Runtime Stage
# ============================================================
FROM python:3.13-slim

WORKDIR /app

# Install only runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder. Destination is /opt/pydeps (not /root):
# the runtime drops to appuser, which cannot traverse /root (drwx------),
# so packages copied under /root/.local are invisible AND unreadable there.
# (That was the `ModuleNotFoundError: No module named 'uvicorn'` crash loop.)
COPY --from=builder --chown=10001:10001 /root/.local /opt/pydeps
ENV PATH=/opt/pydeps/bin:$PATH
ENV PYTHONPATH=/opt/pydeps/lib/python3.13/site-packages

# Copy application (see .dockerignore: data/, logs/, secrets excluded)
COPY . .

# Create data directories + drop root: run as unprivileged user in production.
RUN mkdir -p data/raw data/staging data/curated logs outputs/charts outputs/reports config/pipelines config/schedules \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["python", "run.py", "api"]

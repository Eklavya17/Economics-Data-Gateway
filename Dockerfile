# syntax=docker/dockerfile:1.7
#
# Economics Data Gateway: Streamable HTTP image. Build from this folder (the repository root):
#
#   docker build -t econ-gateway:1.0.0 .
#
# or use docker-compose.yml. Secrets (BLS/FRED keys, JWT settings) are passed at run time as
# environment variables; no .env file is ever copied into the image (.dockerignore).

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Package metadata first, then source: the source changes far more often than dependencies.
# The curated catalog and reference tables ship inside the package (src/econ_gateway/sources/
# reference), so the image needs no other data at build time.
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
RUN pip install -e ".[otel]"

# Unprivileged user; /data is the only writable path (observation cache, OEWS downloads,
# BLS quota count).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin gateway \
 && mkdir -p /data/cache /data/raw /data/state \
 && chown -R gateway:gateway /data
USER gateway
VOLUME ["/data"]

ENV CACHE_DIR=/data/cache \
    RAW_DIR=/data/raw \
    ECON_STATE_DIR=/data/state \
    ECON_TRANSPORT=http \
    ECON_HOST=0.0.0.0 \
    ECON_PORT=8000

EXPOSE 8000

# Liveness probe over loopback (an allowed Host for the Host/Origin guard). No curl in the
# slim image, so use Python's standard library.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# Binding 0.0.0.0 requires authentication: set ECON_AUTH_MODE=jwt plus the ECON_JWT_* settings,
# or ECON_ALLOW_UNAUTHENTICATED_REMOTE=true if a proxy in front authenticates every request.
# Without either, the server refuses to start (docs/DEPLOYMENT.md).
CMD ["econ-gateway", "--transport", "http"]

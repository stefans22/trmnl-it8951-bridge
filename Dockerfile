# syntax=docker/dockerfile:1
# Image for linux/arm64 (Raspberry Pi 3 with a 64-bit OS), see docs/adr/0012-arm64-ghcr.md.

# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

# spidev and gpiod are source-only packages (gpiod bundles libgpiod's C sources).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra hw --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra hw --no-editable

# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="ha-trmnl-ws-display" \
      org.opencontainers.image.description="TRMNL HA add-on images on a Waveshare IT8951 e-paper panel" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --uid 1000 --user-group --no-create-home --home-dir /data app \
    && mkdir -p /data /config \
    && chown app:app /data

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EPD_CONFIG_FILE=/config/config.yaml

USER app
WORKDIR /data
EXPOSE 8080
VOLUME ["/data"]
STOPSIGNAL SIGTERM

# Uses the port from EPD_SERVER__PORT if set; a port set only in the YAML file needs the same env var.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('EPD_SERVER__PORT', '8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"]

CMD ["ha-trmnl-ws-display"]

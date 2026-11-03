# Local Image Studio GPU engine for Linux + NVIDIA: ComfyUI with FLUX.2 Klein 9B (GGUF), SAM 3 and the helper
# that starts the engine on demand. Models are not baked in; they download into ./models on first start.
FROM python:3.12-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache
RUN pip install --no-cache-dir uv==0.13.6
COPY requirements.cuda.lock.txt ./
RUN uv venv --python /usr/local/bin/python3 .venv \
    && uv pip sync --python .venv/bin/python requirements.cuda.lock.txt \
    && rm -rf /tmp/uv-cache
COPY config config
COPY scripts scripts
COPY studio studio
COPY workflows workflows
COPY docker/engine-entrypoint.sh docker/engine-entrypoint.sh
# The image has no GPU while building; the driver is checked when the container starts.
RUN .venv/bin/python scripts/setup_cuda.py --no-models --skip-gpu-check
# A portable SAM 3 binary (no -march=native), then drop its sources and build tools.
RUN cd scripts && SAM3_NATIVE=OFF ../.venv/bin/python -c "from setup_sam3 import build; build()" \
    && cd /app/runtime && mv sam3-build/studio-sam3 /tmp/studio-sam3 \
    && rm -rf sam3-build sam3-build-env sam3.cpp && mkdir sam3-build && mv /tmp/studio-sam3 sam3-build/ \
    && mkdir -p /app/models /app/outputs /app/state /app/.cache \
    && chmod -R a+rwX /app/runtime /app/models /app/outputs /app/state /app/.cache

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg openssl curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app /app
# Runs as the host user from compose (LIS_UID), who may have no home directory in the image.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp HF_HUB_DISABLE_TELEMETRY=1 NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    COMFY_LISTEN=0.0.0.0 HELPER_HOST=0.0.0.0 ENGINE_IDLE_SECONDS=600 SAM3_TIMEOUT=900 \
    LIS_STATE=/app/state SAM3_TOKEN_FILE=/app/state/token/helper-token.txt
EXPOSE 7877 8188
HEALTHCHECK --interval=30s --timeout=5s --start-period=2h --start-interval=10s CMD .venv/bin/python -c "import os,urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:7877/health',headers={'Authorization':'Bearer '+open(os.environ['SAM3_TOKEN_FILE']).read().strip()}),timeout=3)"
ENTRYPOINT ["/app/docker/engine-entrypoint.sh"]

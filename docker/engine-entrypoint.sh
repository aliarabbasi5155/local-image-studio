#!/usr/bin/env bash
# Check (or, on first start, download) the models, then run the helper. The helper starts ComfyUI when a
# job arrives and stops it after ENGINE_IDLE_SECONDS, freeing GPU memory between sessions.
set -euo pipefail
cd /app
source scripts/env.sh
nvidia-smi >/dev/null 2>&1 || ls /dev/nvidia0 >/dev/null 2>&1 || {
  echo 'No NVIDIA GPU is visible in this container. Install the NVIDIA Container Toolkit and use Docker Engine'
  echo '(Docker Desktop for Linux cannot pass GPUs to containers). See docs/install-docker.md.'
  exit 1
}
(cd scripts && ../.venv/bin/python engine_container.py)
exec .venv/bin/python scripts/sam3_service.py

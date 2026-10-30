#!/usr/bin/env bash
# Separate Linux/NVIDIA installation; never sync the Mac's .venv or MLX weights.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
[[ "$(uname -s)" == Linux ]] || { echo 'This installer requires Linux and an NVIDIA GPU.'; exit 1; }
command -v nvidia-smi >/dev/null || { echo 'Install the NVIDIA driver first.'; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install uv
export PATH="$PROJECT_ROOT/.venv/bin:$PATH"
.venv/bin/python -c 'import json; from pathlib import Path; from scripts.setup_runtime import install; install(json.load(open("config/runtime.json"))["comfyui"], Path.cwd()/"runtime/ComfyUI")'
uv pip sync --python .venv/bin/python requirements.cuda.lock.txt
.venv/bin/python scripts/setup_cuda.py
.venv/bin/python scripts/download_loras.py snofs-klein-9b-v1.4
.venv/bin/python scripts/download_upscaler.py
.venv/bin/python scripts/setup_sam3.py
echo 'Ready. Start Docker Desktop, then run: bash scripts/start_linux_docker.sh YOUR_LAN_IP'
echo 'Optional photo-to-video: .venv/bin/python scripts/setup_video_cuda.py'

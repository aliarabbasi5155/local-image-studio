#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
command -v uv >/dev/null || { echo 'Install uv first: brew install uv'; exit 1; }
uv python install 3.12.14
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12.14 .venv
fi
.venv/bin/python scripts/setup_runtime.py
uv pip sync --python .venv/bin/python requirements.lock.txt
if [[ "${1:-}" != "--no-models" ]]; then
  MODEL_KEY="$(.venv/bin/python -c 'import json; print(json.load(open("config/defaults.json"))["model"])')"
  .venv/bin/python scripts/download_models.py "$MODEL_KEY"
fi
echo 'Ready. Start ComfyUI with ./scripts/start.sh'

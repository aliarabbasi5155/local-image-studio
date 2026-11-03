#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$PROJECT_ROOT/outputs" "$PROJECT_ROOT/runtime/user" "$PROJECT_ROOT/runtime/input"
cd "$PROJECT_ROOT/runtime/ComfyUI"
if [[ -f "$PROJECT_ROOT/runtime/backend.txt" ]] && [[ "$(cat "$PROJECT_ROOT/runtime/backend.txt")" == "cuda" ]]; then
  set -- --lowvram --reserve-vram 2 --cache-none --disable-pinned-memory "$@"
fi
exec "$PROJECT_ROOT/.venv/bin/python" main.py \
  --listen "${COMFY_LISTEN:-127.0.0.1}" --port "${COMFY_PORT:-8188}" \
  --output-directory "$PROJECT_ROOT/outputs" \
  --input-directory "$PROJECT_ROOT/runtime/input" \
  --user-directory "$PROJECT_ROOT/runtime/user" \
  --preview-method none --offline "$@"

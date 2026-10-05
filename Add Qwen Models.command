#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/env.sh"
cd "$PROJECT_ROOT"
if ! .venv/bin/python scripts/setup_qwen.py; then
  read -r -p 'Press Return to close.'
  exit 1
fi
.venv/bin/python scripts/launch.py --restart --no-browser
echo 'Qwen is ready. Refresh Image Studio and choose Edit → Model preset.'
read -r -p 'Press Return to close.'

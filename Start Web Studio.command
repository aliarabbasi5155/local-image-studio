#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/env.sh"
cd "$PROJECT_ROOT"
if ! .venv/bin/python scripts/web_launch.py "$@"; then
  read -r -p 'Press Return to close.'
  exit 1
fi

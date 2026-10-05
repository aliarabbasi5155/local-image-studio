#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/env.sh"
cd "$PROJECT_ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo 'The image environment is missing. Run ./scripts/setup.sh first.'
  read -r -p 'Press Return to close.'
  exit 1
fi
if ! .venv/bin/python scripts/launch.py "$@"; then
  read -r -p 'Press Return to close.'
  exit 1
fi

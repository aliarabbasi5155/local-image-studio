#!/usr/bin/env bash
# macOS: double-click to start Local Image Studio and open it in your browser.
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Local Image Studio is not installed yet. Open Terminal in this folder and run: ./install.sh'
  read -r -p 'Press Return to close.'
  exit 1
fi
source scripts/env.sh
if ! .venv/bin/python scripts/launcher.py start "$@"; then
  read -r -p 'Press Return to close.'
  exit 1
fi

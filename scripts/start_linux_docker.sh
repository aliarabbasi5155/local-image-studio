#!/usr/bin/env bash
# Run once to create the Compose app; later use Start/Stop in Docker Dashboard.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_ROOT"
address="${1:-$(hostname -I | awk '{print $1}')}"
[[ -f runtime/backend.txt && "$(cat runtime/backend.txt)" == cuda ]] || { echo 'Run scripts/setup_linux.sh first.'; exit 1; }
.venv/bin/python scripts/setup_linux_service.py --address "$address"
mkdir -p studio-data
export ENGINE_PROJECT_ROOT="$PROJECT_ROOT"
export STUDIO_UID="$(id -u)" STUDIO_GID="$(id -g)"
export STUDIO_LAN_URL="https://$address:7443"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-desktop-linux}"
docker info >/dev/null 2>&1 || { echo 'Open Docker Desktop, then run this command again.'; exit 1; }
docker compose -f compose.yaml -f compose.linux.yaml up -d --build --wait --wait-timeout 60
echo "Studio: http://$address:7878 (phone setup), https://$address:7443"
echo "PIN: $(cat studio-data/access-pin.txt)"

#!/usr/bin/env bash
# Local Image Studio installer for macOS (Apple Silicon) and Linux (NVIDIA GPU).
#
#   ./install.sh                 install everything and start the studio
#   ./install.sh --with-snofs    also install the optional SNOFS style adapter (personal-use license)
#   ./install.sh --with-video    also install photo-to-video (about 25 GB more)
#   ./install.sh --no-models     install the software only
#   ./install.sh --docker-website  (macOS) serve the website from Docker Desktop instead
#
# Without cloning first:
#   curl -fsSL https://raw.githubusercontent.com/aliarabbasi5155/local-image-studio/main/install.sh | bash
#
# Running it again is safe: finished steps are verified and skipped. Everything (Python, packages,
# models, caches) stays inside this folder.
set -euo pipefail

REPOSITORY="https://github.com/aliarabbasi5155/local-image-studio.git"
UV_VERSION="0.13.6"
PYTHON_VERSION="3.12.14"

bold=$'\033[1m' red=$'\033[31m' dim=$'\033[2m' reset=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
note() { printf '%s%s%s\n' "$dim" "$*" "$reset"; }
fail() { printf '\n%sCannot continue:%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

with_snofs=0 with_video=0 models=1 website="" start=1
for argument in "$@"; do
  case "$argument" in
    --with-snofs) with_snofs=1 ;;
    --with-video) with_video=1 ;;
    --no-models) models=0 ;;
    --docker-website) website=docker ;;
    --no-start) start=0 ;;
    -h|--help) sed -n '2,14p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "Unknown option: $argument (see --help)" ;;
  esac
done

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) platform=mac ;;
  Linux/x86_64) platform=cuda ;;
  Darwin/*) fail "This Mac has an Intel processor. Local Image Studio needs Apple Silicon (M1 or newer)." ;;
  *) fail "Local Image Studio runs on a Mac with Apple Silicon or a Linux PC with an NVIDIA GPU." ;;
esac

step "Checking this computer"
if [[ $platform == mac ]]; then
  major="$(sw_vers -productVersion | cut -d. -f1)"
  (( major >= 14 )) || fail "Update macOS to version 14 (Sonoma) or newer."
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    fail "Apple's Command Line Tools are needed. Finish the installation window that just opened, then run this again."
  fi
  memory_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  (( memory_gb >= 24 )) || note "This Mac has ${memory_gb} GB of memory; 24 GB or more is recommended. Use Fast quality if images fail."
  needed_gb=35
else
  command -v nvidia-smi >/dev/null || fail "Install the NVIDIA driver (version 580 or newer), restart, then run this again."
  driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  [[ -n $driver ]] || fail "nvidia-smi cannot see a GPU. Check the NVIDIA driver installation."
  (( ${driver%%.*} >= 580 )) || fail "NVIDIA driver $driver is too old for CUDA 13. Install driver 580 or newer."
  missing=()
  for tool in git curl make c++; do command -v "$tool" >/dev/null || missing+=("$tool"); done
  if (( ${#missing[@]} )); then
    fail "Missing ${missing[*]}. On Ubuntu or Debian: sudo apt install git curl build-essential"
  fi
  needed_gb=40
fi
command -v git >/dev/null || fail "Install Git, then run this again."
(( with_video )) && needed_gb=$(( needed_gb + 25 ))

# Piped from curl: fetch the project, then continue with its own copy of this script.
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
if [[ ! -f $here/scripts/env.sh ]]; then
  target="${LIS_HOME:-$HOME/LocalImageStudio}"
  step "Downloading Local Image Studio into $target"
  if [[ -d $target/.git ]]; then git -C "$target" pull --ff-only; else git clone --depth 1 "$REPOSITORY" "$target"; fi
  exec bash "$target/install.sh" "$@"
fi
cd "$here"
source scripts/env.sh

free_gb=$(( $(df -Pk . | awk 'NR==2 {print $4}') / 1048576 ))
if (( models )) && [[ ! -d models/flux2-klein-9b-8bit && ! -d models/cuda ]] && (( free_gb < needed_gb )); then
  fail "About ${needed_gb} GB of free disk space is needed here; ${free_gb} GB is free."
fi

if command -v uv >/dev/null; then
  uv=uv
elif [[ -x .cache/bin/uv ]]; then
  uv=.cache/bin/uv
else
  step "Installing uv (Python package manager) inside this folder"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR="$PWD/.cache/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
  uv=.cache/bin/uv
fi
export PATH="$PWD/.cache/bin:$PATH"

step "Preparing Python $PYTHON_VERSION"
"$uv" python install "$PYTHON_VERSION"
[[ -x .venv/bin/python ]] || "$uv" venv --python "$PYTHON_VERSION" .venv

if [[ $platform == mac ]]; then
  step "Installing the image engine (ComfyUI with Apple MLX)"
  .venv/bin/python scripts/setup_runtime.py
  "$uv" pip sync --python .venv/bin/python requirements.lock.txt
  if (( models )); then
    step "Downloading FLUX.2 Klein 9B (8-bit 17.9 GB + 4-bit 9.5 GB; this takes a while)"
    .venv/bin/python scripts/download_models.py flux2-klein-9b-8bit flux2-klein-9b-4bit
  fi
else
  step "Installing the image engine (ComfyUI with CUDA)"
  "$uv" pip sync --python .venv/bin/python requirements.cuda.lock.txt
  if (( models )); then
    step "Downloading FLUX.2 Klein 9B and its text encoder (about 25 GB; this takes a while)"
    .venv/bin/python scripts/setup_cuda.py
  else
    .venv/bin/python scripts/setup_cuda.py --no-models
  fi
fi

if (( models )); then
  step "Installing the AI upscaler and automatic selection (SAM 3, 1.1 GB)"
  .venv/bin/python scripts/download_upscaler.py
  .venv/bin/python scripts/setup_sam3.py
fi

if (( with_snofs )); then
  step "Installing the SNOFS style adapter (1.1 GB)"
  note "SNOFS is a third-party adapter under its author's Model Personal Use License: local use only,"
  note "no hosting it as a service for others, no redistribution. Terms: https://huggingface.co/Ashen3/SNOFS"
  .venv/bin/python scripts/download_loras.py snofs-klein-9b-v1.4
fi

if (( with_video )); then
  step "Installing photo-to-video (LTX-2.3, about 25 GB)"
  if [[ $platform == mac ]]; then
    command -v ffmpeg >/dev/null || fail "Photo-to-video needs FFmpeg: brew install ffmpeg (https://brew.sh), then run this again."
    .venv/bin/python scripts/setup_video.py
  else
    command -v ffmpeg >/dev/null || fail "Photo-to-video needs FFmpeg: sudo apt install ffmpeg"
    .venv/bin/python scripts/setup_video_cuda.py
  fi
fi

if (( start && models )); then
  step "Starting Local Image Studio"
  arguments=(start)
  [[ -n $website ]] && arguments+=("--$website")
  [[ $platform == cuda && -z ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]] && arguments+=(--no-browser)
  .venv/bin/python scripts/launcher.py "${arguments[@]}"
else
  step "Installed"
  echo "Start the studio with: make start"
fi

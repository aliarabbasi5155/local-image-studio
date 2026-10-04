#!/usr/bin/env bash
# Keep downloaded runtimes, models and application state inside this repository.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export PROJECT_ROOT
export UV_CACHE_DIR="$PROJECT_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.cache/python"
export UV_PYTHON_BIN_DIR="$PROJECT_ROOT/.cache/bin"
export HF_HOME="$PROJECT_ROOT/.cache/huggingface"
export MFLUX_CACHE_DIR="$PROJECT_ROOT/.cache/mflux"
export XDG_CACHE_HOME="$PROJECT_ROOT/.cache"
export MPLCONFIGDIR="$PROJECT_ROOT/.cache/matplotlib"
export TOKENIZERS_PARALLELISM=false

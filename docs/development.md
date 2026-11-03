# Development

## Repository layout

| Path | Purpose |
| --- | --- |
| `install.sh` | Installer for macOS and Linux |
| `Makefile` | Everyday commands (`make help`) |
| `Local Image Studio.command` | macOS double-click launcher |
| `compose.yaml`, `.env.example` | Linux + NVIDIA Docker stack |
| `docker/` | Website and engine images, the engine entrypoint, the macOS website compose file |
| `studio/` | Website: `server.py` (HTTP API), `engine.py` (ComfyUI workflows and the job worker), `masking.py`, `closeup.py`, `identity.py`, `video.py`, `storage.py`, `static/` (front end) and `tests/` |
| `scripts/` | Installer steps, the launcher (`launcher.py`), login services (`services.py`), the helper (`sam3_service.py`, `engine_supervisor.py`), video workers and the command-line generator |
| `config/` | Pinned model, runtime and adapter sources |
| `patches/` | Memory, LoRA and editing patches for the MLX ComfyUI node |
| `workflows/` | ComfyUI workflows. The website builds on `01 - Create Images` and `02 - Edit Images`. |
| `examples/` | A sample photo for the editing workflow and two sample outputs |

## Tests and checks

```bash
make test    # Python tests (API, persistence, masking, identity, video, CUDA memory) and Node front-end tests
make check   # Python and JavaScript syntax, shell syntax, dependency compatibility, whitespace
```

The tests don't need a GPU or downloaded models; they use fake engines. Node.js is needed for the
front-end tests.

To run a website against a separate, throwaway library while the real studio keeps running:

```bash
STUDIO_DATA=/tmp/studio-test ENGINE_CONTROL_URL=http://127.0.0.1:7877 \
  .venv/bin/python -m studio.server --host 127.0.0.1 --port 7990 --https-port 7444
```

## Command line and ComfyUI

`make comfyui` runs ComfyUI's own interface at <http://127.0.0.1:8188> (in the foreground; stop it with
**Ctrl+C**). If the studio's engine is already awake, it is at the same address. The **Workflows**
sidebar has **01 - Create Images**, **02 - Edit Images** and a workflow per model; the `-snofs` variants
need the [SNOFS adapter](models-and-licenses.md#snofs-adapter).

With the engine running, `scripts/generate.py` makes an image and checks the result:

```bash
source scripts/env.sh
.venv/bin/python scripts/generate.py --prompt "A red fox in fresh snow" --seed 123
.venv/bin/python scripts/generate.py --model flux2-klein-9b-4bit --width 768 --height 768
.venv/bin/python scripts/generate.py --model flux2-klein-4b-8bit   # Apache-2.0 4B model; download it first:
.venv/bin/python scripts/download_models.py flux2-klein-4b-8bit
```

Each run checks the server result, dimensions, nonblank pixels and PNG metadata, and writes a timing
report to `benchmarks/`. `make benchmark` makes two images with different seeds.

## Adding a model family

1. Pin its source in `config/models.json` (or `config/cuda.json` for NVIDIA), with revision, size and
   license.
2. Add its ComfyUI integration to the runtime setup (`scripts/setup_runtime.py` or `scripts/setup_cuda.py`)
   and a workflow in `workflows/`.
3. Keep weights in `models/`, benchmark on the target computer, then wire it into
   `studio/engine.py` (`QUALITIES`, `build_graph`).

## Updating pinned versions

- Python packages: update `requirements.lock.txt` (macOS) or `requirements.cuda.lock.txt` (NVIDIA)
  and run `./install.sh`, which syncs the environment exactly.
- ComfyUI and nodes: change the commits in `config/runtime.json` / `config/cuda.json`. The installer
  refuses to move a runtime that has local changes, so they are never overwritten silently.
- MLX node patches: `scripts/setup_runtime.py` checks the whole patch stack on a copy before touching
  the installed node. If upstream changes, regenerate the patches against the new commit.

## Docker images

```bash
docker compose build                                          # Linux: website + CUDA engine
docker build -f docker/studio.Dockerfile -t local-image-studio .  # website only
```

The engine image builds without a GPU (`setup_cuda.py --skip-gpu-check`) and compiles SAM 3 without
`-march=native` (`SAM3_NATIVE=OFF`), so it runs on any x86-64 CPU. Models are never baked into images.

## Contributing

Issues and pull requests are welcome. Please run `make check` and `make test` before opening a pull
request. Describe what you tested on real hardware (Mac or NVIDIA), since the tests use fake engines.

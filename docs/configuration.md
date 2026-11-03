# Configuration

Local Image Studio works without configuration. This page covers what you can change and where
everything lives.

## Where everything is saved

| Path | Contents |
| --- | --- |
| `studio-data/` | **Your library.** `studio.sqlite3` (projects, recipes, favorites, sign-ins), `images/`, `uploads/`, `videos/`, `thumbs/`, `masks/` and `access-pin.txt`. |
| `models/` | Downloaded model weights. See [models/README.md](../models/README.md). |
| `runtime/` | ComfyUI and other downloaded runtimes, the SAM 3 build, the helper token, certificates (`home-ca/`, `tls/`) and logs. |
| `outputs/` | The engine's own copies of finished images. The library doesn't need them. |
| `.venv/`, `.cache/` | Python, packages and download caches for this folder only. |

Nothing is stored outside the project folder except the login services (macOS:
`~/Library/LaunchAgents/com.localimagestudio.*.plist`; Linux: `~/.config/systemd/user/local-image-studio-*.service`).
In Docker, the same folders are used: `models/`, `studio-data/` and `runtime/docker/`.

### Back up your library

Let current images finish, run `make stop` (Docker: `docker compose stop`), copy the whole
`studio-data/` folder, then `make start` again. To restore, put the folder back before starting.

## Change the PIN

Delete `studio-data/access-pin.txt` and restart the website (`make stop`, then `make start`; Docker:
`docker compose restart studio`). A new random PIN is created and printed. Devices that are already
signed in stay signed in.

## Ports

| Port | Service | Reachable from |
| --- | --- | --- |
| 7878 | Website (HTTP) | Your network. Other devices only get the phone setup page once HTTPS is set up. |
| 7443 | Website (HTTPS) | Your network, when a home certificate exists |
| 7877 | Helper: engine control, automatic selection, video | This computer only, and only with the helper token |
| 8188 | Image engine (ComfyUI) | This computer only |

Every website page and API call except setup requires the PIN. The full studio works over plain HTTP
only at `localhost`; phones use HTTPS so the PIN is never sent unencrypted.

## Switch how the website runs (macOS)

```bash
make start-native   # website runs directly on the Mac (default)
make start-docker   # website runs in Docker Desktop; the engine stays native
```

The choice is remembered in `runtime/website-mode.txt`. Both use the same library and PIN.

## Environment variables

You rarely need these; the launcher and Compose set them. They are listed for custom setups.

**Website** (`python -m studio.server [--host 0.0.0.0] [--port 7878] [--https-port 7443]`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `STUDIO_DATA` | `studio-data/` | Library folder |
| `STUDIO_PORT` | `7878` | HTTP port |
| `STUDIO_TLS_DIR` | `runtime/tls/` | HTTPS starts when this folder contains `server.crt` |
| `STUDIO_LAN_URL` | the request's address | Address shown for phones |
| `COMFY_URL` | `http://127.0.0.1:8188` | Image engine |
| `ENGINE_CONTROL_URL` | none | Helper address. When set, jobs wake the engine on demand. |
| `ENGINE_PROJECT_ROOT` | this folder | Engine's project folder, when the website runs in a container |
| `SAM3_URL` | `http://127.0.0.1:7877` | Automatic selection (the helper) |
| `SAM3_TOKEN_FILE` | `runtime/sam3-token.txt` | Helper token |
| `SAM3_TIMEOUT` | `170` | Seconds before automatic selection gives up (900 on Linux, where it runs on the CPU) |
| `STUDIO_BACKEND` | detected | `cuda` for NVIDIA engines |

**Helper** (`scripts/sam3_service.py`) and **engine** (`scripts/start.sh`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENGINE_IDLE_SECONDS` | `600` | Idle time before the engine stops and frees memory |
| `HELPER_HOST` | `127.0.0.1` | Helper listen address (`0.0.0.0` inside the Docker network) |
| `COMFY_LISTEN`, `COMFY_PORT` | `127.0.0.1`, `8188` | Engine listen address and port |

For the Docker settings in `.env`, see [Run with Docker](install-docker.md#settings).

## Models and pins

| File | Pins |
| --- | --- |
| `config/models.json` | MLX image models |
| `config/cuda.json` | NVIDIA image models, text encoder, VAE and ComfyUI-GGUF |
| `config/loras.json` | Optional adapters |
| `config/sam3.json` | SAM 3 source, model and build tools |
| `config/upscaler.json` | Real-ESRGAN |
| `config/video.json`, `config/video.cuda.json` | Photo to video |
| `config/runtime.json` | ComfyUI and the MLX node commits |
| `requirements.lock.txt`, `requirements.cuda.lock.txt` | Exact Python packages for macOS and NVIDIA |

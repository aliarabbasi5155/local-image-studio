# Run with Docker

| Your computer | What runs in Docker |
| --- | --- |
| Linux with an NVIDIA GPU | Everything: the website and the GPU engine. Start it with `docker compose up -d`. |
| Windows with an NVIDIA GPU | Everything, using Docker Desktop with WSL 2. Use the [Windows installer](install-windows.md), which uses `compose.windows.yaml` and named volumes. |
| Mac with Apple Silicon | Only the website. Docker on macOS can't use the Apple GPU, so the engine runs natively. [See below](#macos-website-in-docker-desktop). |

## Linux with an NVIDIA GPU

### Requirements

- An NVIDIA GPU with 6 GB of VRAM or more, and **driver 580 or newer**. Check with `nvidia-smi`.
- **Docker Engine** with the Compose plugin: [install Docker Engine](https://docs.docker.com/engine/install/).
- The **NVIDIA Container Toolkit**: [installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
  After installing it, run `sudo nvidia-ctk runtime configure --runtime=docker` and `sudo systemctl restart docker`.
- About 45 GB of free disk space (models plus the engine image).

Docker Desktop for Linux [can't give containers a GPU](https://docs.docker.com/desktop/features/gpu/).
If you have both, use Docker Engine's context: `docker context use default`.

Check that containers can see the GPU:

```bash
docker run --rm --gpus all ubuntu nvidia-smi
```

### Start

```bash
git clone https://github.com/aliarabbasi5155/local-image-studio.git
cd local-image-studio
cp .env.example .env     # optional: set LIS_ADDRESS to turn on the phone app
docker compose up -d
docker compose logs -f engine
```

The first start builds the images (about 10 minutes) and downloads and verifies about 27 GB of models
into `./models`. When the log says **Models are ready**, open <http://localhost:7878>. The PIN is in
`studio-data/access-pin.txt`:

```bash
cat studio-data/access-pin.txt
```

The containers restart with Docker, so the studio comes back after a reboot. The engine container
holds no model in GPU memory until you make an image, and frees it after 10 idle minutes.

### Settings

Copy `.env.example` to `.env` and edit it. The main settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `LIS_ADDRESS` | empty | This computer's home-network address (`hostname -I`). Turns on the installable phone app over HTTPS. |
| `LIS_UID`, `LIS_GID` | `1000` | Owner of `models/`, `studio-data/` and `runtime/docker/`. Set them to `id -u` and `id -g`. |
| `LIS_SNOFS` | `0` | `1` downloads the optional SNOFS adapter on the next start. |
| `LIS_VIDEO` | `0` | `1` downloads photo-to-video (about 25 GB) on the next start. |
| `LIS_BIND` | `0.0.0.0` | `127.0.0.1` allows only this computer to connect. |
| `LIS_PORT`, `LIS_HTTPS_PORT` | `7878`, `7443` | Website ports. |

Apply changes with `docker compose up -d`.

### Everyday commands

```bash
docker compose ps                 # status
docker compose logs -f            # logs
docker compose stop               # stop; `docker compose start` to resume
git pull && docker compose up -d --build   # update
docker compose down               # remove the containers; models and library stay
```

### What's where

| Host folder | Contents |
| --- | --- |
| `models/` | Downloaded model weights (reused across rebuilds) |
| `studio-data/` | Your library, recipes and PIN. Back up this folder. |
| `runtime/docker/` | Helper token, home certificate authority and website certificate |

The engine image contains ComfyUI, its GGUF nodes, PyTorch with CUDA and a portable SAM 3 build. The
engine and helper ports (8188, 7877) are reachable only inside the Compose network; the helper also
requires a token.

## macOS: website in Docker Desktop

The standard Mac install runs the website natively and doesn't need Docker. If you prefer the website
in a container (for example, to cap its memory), install normally and then switch:

```bash
./install.sh --docker-website   # or, after installing: make start-docker
```

The image engine and its helper keep running natively. The container reaches them through
`host.docker.internal` with the helper token, and stores the library in the same `studio-data/`
folder. **Local Image Studio** appears in Docker Desktop's Containers list, where you can stop and
start it. Switch back with `make start-native`.

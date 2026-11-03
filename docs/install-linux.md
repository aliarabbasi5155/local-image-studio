# Install on Linux (from source)

This installs Local Image Studio directly on the computer, with no Docker. The website and a small
helper run as systemd user services; the helper starts the GPU engine on demand. For a container
setup instead, see [Docker](install-docker.md).

## Requirements

| | Minimum | Tested |
| --- | --- | --- |
| GPU | NVIDIA, 6 GB VRAM | RTX 3060 Laptop, 6 GB |
| Driver | 580 or newer (CUDA 13) | 580 |
| Memory | 16 GB RAM | 16 GB |
| System | x86-64 Linux with systemd | Ubuntu 24.04 |
| Disk | about 40 GB free | |

Packages, on Ubuntu or Debian:

```bash
sudo apt install git curl build-essential
# Optional, for photo to video:
sudo apt install ffmpeg
```

Install the NVIDIA driver from your distribution (on Ubuntu: **Software & Updates → Additional
Drivers**, or `sudo ubuntu-drivers install`) and restart. `nvidia-smi` should list your GPU.

Python is not required: the installer downloads its own Python 3.12 into the project folder.

## Install

```bash
git clone https://github.com/aliarabbasi5155/local-image-studio.git
cd local-image-studio
./install.sh
```

Or, without cloning first (installs into `~/LocalImageStudio`):

```bash
curl -fsSL https://raw.githubusercontent.com/aliarabbasi5155/local-image-studio/main/install.sh | bash
```

The installer:

1. Checks the driver, tools and disk space, and explains anything missing.
2. Installs Python 3.12, ComfyUI with GGUF support and the pinned packages (`requirements.cuda.lock.txt`).
3. Downloads and verifies FLUX.2 Klein 9B (GGUF Q4_K_M and Q8_0), the Qwen3 8B text encoder, the
   VAE, the Real-ESRGAN upscaler and SAM 3, about 27 GB in total.
4. Builds SAM 3 for the CPU and installs the `local-image-studio-helper` and
   `local-image-studio-web` user services.
5. Creates a home HTTPS certificate for your phone and prints the address and PIN.

Add `--with-video` for photo to video (about 25 GB) or `--with-snofs` for the optional SNOFS
adapter. Running `./install.sh` again is safe; verified downloads are skipped.

## Use it

Open <http://localhost:7878> and enter the PIN (`make pin` prints it). Phones on the same network use
the address the installer printed; see [Use it on your phone](phone.md).

```bash
make status      # what is running
make logs        # follow the website, helper and engine logs
make stop        # stop until the next login or `make start`
make start       # start again (after an address change, this also renews the certificate)
make update      # pull the newest version and reinstall
make uninstall   # remove the services; models and library stay
```

User services run while you are logged in. To keep the studio available after logging out (for
example on a headless machine), enable lingering once:

```bash
sudo loginctl enable-linger "$USER"
```

## How the GPU is used

Fast quality uses the Q4_K_M model; Balanced and High use Q8_0. The engine reserves 2 GB of VRAM for
activations, offloads weights to system memory and decodes the VAE in tiles, so it fits a 6 GB GPU. If
sampling still runs out of GPU memory, it clears unused weights and retries once with full
offloading. The retry is slower but produces the same model, seed, steps and size.

Quantized GGUF weights and CUDA kernels differ from the Mac's MLX build, so the same seed gives a
similar image, not an identical one. Each recipe records the exact model sources.

Automatic selection (SAM 3) runs on the CPU on Linux, so it takes longer than on a Mac.

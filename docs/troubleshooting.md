# Troubleshooting

Start with `make status`, which shows whether the website, helper and engine are running, and
`make logs`, which follows their logs (`runtime/web-studio.log`, `runtime/sam3-service.log`,
`runtime/comfyui-launcher.log`). On Docker, use `docker compose ps` and `docker compose logs -f`.

## Installing

**"Apple's Command Line Tools are needed."** Click **Install** in the window that opened, wait for it to
finish, then run the installer again.

**The download stopped or failed.** Run `./install.sh` again. Finished files are verified and skipped,
and partial downloads continue.

**"About N GB of free disk space is needed."** Free up space, or install into a folder on a larger
disk:

```bash
curl -fsSL https://raw.githubusercontent.com/aliarabbasi5155/local-image-studio/main/install.sh | LIS_HOME=/Volumes/Big/LocalImageStudio bash
```

**Linux: "NVIDIA driver … is too old."** CUDA 13 needs driver 580 or newer. Update it from your
distribution's driver tool and restart.

## Opening the studio

**The page doesn't load.** Run `make start`; it starts anything that's stopped and prints the address.

**"Port 7878 is used by another application."** Another program uses the port. Close it, then run
`make start`.

**I forgot the PIN.** `make pin`, or double-click **Local Image Studio.command** on a Mac. On Docker:
`cat studio-data/access-pin.txt`.

**"The helper needs an update."** After an update, the helper restarts only when no image is being
made. Wait for current images to finish, then run `make start` again.

## Making images

**The first image is slow.** The engine loads the model the first time and after 10 idle minutes.
Later images are faster.

**An image failed or the computer ran out of memory.** Close other large applications and try
**Fast** quality, which uses the smaller 4-bit model. Large 2K/4K outputs and **None · full size**
need the most memory. On NVIDIA, the engine already retries once with more offloading.

**"Your computer's background helper is unavailable."** The helper isn't running. Run `make start`. On
Linux, the helper runs as a user service: check `systemctl --user status local-image-studio-helper`.

**Automatic selection is unavailable.** SAM 3 may not have built. Run `./install.sh` again and read the
"Building automatic selection" step. You can always paint areas by hand.

**Automatic selection says the computer is busy.** Selection waits for image generation to finish,
because both use the GPU.

## Phones

**The phone can't open the address.** The phone must be on the same Wi-Fi network (not a guest
network). Check that your computer's firewall allows incoming connections:

- macOS: **System Settings → Network → Firewall → Options**: allow incoming connections for Python.
- Ubuntu: `sudo ufw allow 7878/tcp && sudo ufw allow 7443/tcp`.

**The address changed.** Run `make start`. It prints the new address and renews the certificate.
Reserve the address in your router to avoid this.

**The app won't install, or shows a certificate warning.** The certificate must be fully trusted, not
just installed: on iPhone, also enable it in **Settings → General → About → Certificate Trust
Settings**. On Android, install it as a **CA certificate** and restart Chrome. See
[Use it on your phone](phone.md#install-it-as-an-app).

## Docker (Linux)

**"No NVIDIA GPU is visible in this container."** Install the NVIDIA Container Toolkit, run
`sudo nvidia-ctk runtime configure --runtime=docker` and restart Docker. Docker Desktop for Linux
can't pass GPUs to containers; use Docker Engine (`docker context use default`). Test with
`docker run --rm --gpus all ubuntu nvidia-smi`.

**"Permission denied" in `models/` or `studio-data/`.** Set `LIS_UID` and `LIS_GID` in `.env` to the
output of `id -u` and `id -g`, then `docker compose up -d`.

**The website says the helper is unavailable right after starting.** On the first start, the engine
container downloads about 27 GB before its helper starts. Follow it with `docker compose logs -f engine`.

## Start over

`make uninstall` removes the login services and keeps your library and models. Deleting `runtime/`
and running `./install.sh` rebuilds all runtimes (models in `models/` are reused). If you delete the
certificate folders, phones need to trust the new certificate.

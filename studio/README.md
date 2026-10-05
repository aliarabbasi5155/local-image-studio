# Image Studio

A small website for the existing FLUX.2 Klein / SNOFS setup. Use it on this Mac
or a phone on the same Wi-Fi. It includes image creation and editing, 1–4
variations, quality presets, Favorites, a reversible Archive, and saved recipes.

## Open it again

1. Open **Docker Desktop** and wait until it is running.
2. Double-click **Start Web Studio.command** in the project folder.
3. Enter the six-digit PIN printed in the launcher. It is also saved in
   `studio-data/access-pin.txt` and stays the same after a restart.
4. On this Mac, open **http://localhost:7878**. On your phone, open the
   **On your phone** address printed by the launcher while using the same Wi-Fi.
   The **Connect** button in the website also shows that address.

You can close the launcher Terminal window and browser. Keep the Mac awake and
Docker Desktop running while making images. After restarting the Mac, launch
the studio again. If the router changes the Mac's address, the next launch
prints the new phone address. Your phone can add the page to its home screen
from the browser's Share/menu controls.

## Make and edit images

- **Create:** describe what you want, choose quality, shape and 1–4 variations,
  then tap **Create image**. Each variation uses a different seed and saves as
  it finishes. Images run one at a time to fit this Mac's memory.
- **Edit:** choose **Edit image**, upload a PNG, JPEG or WebP photo, and describe
  the change. Or open a library image and choose **Edit this image**. Tell the
  model what should stay the same, for example: “Make the coat red. Keep the
  same face, scarf and background.” Your source remains saved.
- **Heart:** adds an image to Favorites. Tap it again to remove the favorite.
- **Archive:** moves an image out of the library. Open Archive and choose
  **Restore** to bring it back. Its image, source and settings are kept.
- **Open an image:** download its PNG, view its settings, download the recipe,
  reuse its settings, or make another edit. **Reuse settings** restores the
  prompt, seed, quality, shape, SNOFS and editing source for that result.
- **Stop:** stops only that variation. Other queued variations can finish.

AI edits can also change details you asked to keep; inspect the result. The
upload accepts photos up to 20 MB and 16 megapixels. Convert HEIC photos to
JPEG or PNG before uploading.

## Quality

| Preset | Model | Square | Portrait | Landscape |
| --- | --- | --- | --- | --- |
| Fast | Klein 9B, 4-bit | 512 × 512 | 512 × 640 | 768 × 512 |
| Balanced | Klein 9B, 8-bit | 768 × 768 | 640 × 800 | 960 × 640 |
| High | Klein 9B, 8-bit | 1024 × 1024 | 832 × 1040 | 1152 × 768 |

Start with **Fast** to explore ideas, then use Balanced or High for more detail.
All presets use the distilled model's four steps and guidance 1.0. SNOFS is
enabled by default and can be switched off. The optional fixed seed makes it
easy to revisit the same settings; random seed gives a fresh starting point.

Two real Fast square creations with SNOFS took about 21 and 19 seconds on this
Mac, and a Fast square edit took about 30 seconds. Larger images and other
applications change timing. These are measured examples, not guarantees.

## Where everything is saved

`studio-data/` is the persistent library, outside the container:

| Path | Contents |
| --- | --- |
| `studio.sqlite3` | Jobs, recipes, favorite/archive state and sign-in sessions |
| `images/` | Original generated PNGs, with embedded recipe and ComfyUI workflow |
| `uploads/` | Saved editing sources, normalized to PNG |
| `thumbs/` | Small gallery previews |
| `access-pin.txt` | Your local access PIN |

Restarting or rebuilding the container preserves this directory. To back up the
library, wait for generations to finish, stop the website with the command
below, then copy the **whole studio-data folder**. Start it again with the
launcher. Git ignores your images, PIN, sessions and model weights.

The website PIN controls access to the library and generation API. This setup
serves HTTP for your trusted local network; the PIN is not encrypted in transit.
Keep access on your local network.

## Docker and developer commands

The launcher starts/reuses ComfyUI natively on the Mac, then builds and runs the
website in Docker. Docker Desktop's documented GPU support does not provide
Apple Metal GPU access to Linux containers, so the MLX engine stays native.
The container reaches it through `host.docker.internal`; the engine itself stays
on loopback port 8188. See [Docker GPU support](https://docs.docker.com/desktop/features/gpu/)
and [host networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/).
Existing model files are reused and are not copied into the Docker image.

From the project root:

```bash
# Start engine + Docker website, without opening a browser
./Start\ Web\ Studio.command --no-browser

# Stop the website (keeps the library and native engine)
docker compose --env-file runtime/studio-docker.env stop

# Inspect website logs
docker compose --env-file runtime/studio-docker.env logs --tail 100

# Run the website directly in this project's Python environment
./Start\ Web\ Studio.command --native

# API, persistence, cancellation and engine integration checks
make test-studio
make check
```

Use one website instance per library; stop Docker before switching to native
mode. Native mode uses the same data and PIN. It requires the dependencies in
`studio/requirements.txt`, already available in this checkout's `.venv`.
For a fresh checkout, install the model/runtime setup described in the root
README, download the optional 4-bit model and SNOFS, then use the web launcher.

The Docker environment is generated in `runtime/studio-docker.env`, including
the absolute host model path and your UID/GID for the data mount. Compose can
also be run manually with that file. `COMFY_URL`, `ENGINE_PROJECT_ROOT`,
`STUDIO_DATA` and `STUDIO_LAN_URL` configure the Python service.

The worker saves each ComfyUI prompt ID before submitting and recovers existing
queued/running/completed jobs after a website restart. If the engine loses a
job's history, it shows an error with **Reuse settings** rather than silently
duplicating the generation. No external services or CDNs are required by the
website. The existing model and adapter license terms still apply.

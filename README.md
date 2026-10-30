# LocalImageModels

Local image generation with ComfyUI on Apple Silicon. The first working model is
**FLUX.2 Klein 9B distilled, 8-bit, using MLX/Metal** on a **Mac M5 with 24 GB RAM**.

The default produces **1024 × 1024** images with **4 steps** and **guidance 1.0**.
Measured end-to-end generation times were **75.66 s** and **64.83 s**, both below
the 90-second target. The measured peak MLX allocation was **11.47 GB**; macOS,
other applications and allocator caches use additional memory. Timings vary with
system load, prompt and resolution.

![Verified FLUX.2 Klein 9B 8-bit output](examples/flux2-klein-9b-8bit.png)

## Run

For the phone-friendly website, open Docker Desktop and double-click
**`Start Web Studio.command`**. It opens **http://localhost:7878** and prints your
local PIN and phone address. Create or edit images with 1–4 variations, choose
Fast / Balanced / High quality, keep an edit's original dimensions or choose
2K/4K output, favorite results, archive and restore images, delete unwanted
results after confirmation,
and reuse the settings saved with every image. The library persists in
`studio-data/`. See [the website guide](studio/README.md).

Phones can install the studio as a home-network-only app. Open the printed phone
address once; it walks the phone through trusting this Mac's local HTTPS
certificate. Choose **Check installation** to verify the secure connection on
port **7443**, then add the studio to the Home Screen.
See [Install it on your phone](studio/README.md#install-it-on-your-phone).

After the first setup, the Mac helper starts at login. Just open the website with
Docker running: submitting an image wakes the engine, and 10 idle minutes shuts
it down to free model memory. Browsing your library keeps the engine asleep.

The library groups originals and their edits into projects, including upload-only
photos. Its fullscreen editor supports parent/original comparison, pinch zoom,
branching edit histories, and per-image deletion. Compare can be toggled, controls
fade in and out, and tapping the photo edges moves between images and projects.
Projects sort by their latest edit; Favorites shows individual images. Selection
tools provide bulk favorites, deletion and full-size PNG/JPG ZIP downloads.
Measured engine-step progress, timing from similar completed runs and a server
status panel show the Mac's work and available memory.
For local changes, Paint an area adds an optional brush mask. The editor works on
a crop and blends the selected area back into the original, preserving unpainted
pixels and the original dimensions. Masks are saved with submitted image recipes.
**Close-up** makes a separate image of a framed area: drag a box or name a subject
and choose Find. It blends the photo's structure with model-generated fine detail,
with Standard, 2K and 4K output and an aligned plain-enlargement comparison.
See [Close-up](studio/README.md#close-up) for fidelity and size tradeoffs.
**Identity swap** replaces the head (face and hair) or the whole person using
another photo. SAM 3 finds both people, a pink preview shows
what may change, and every other pixel stays exact. See
[Identity swap](studio/README.md#identity-swap).

The website runs in Docker; ComfyUI runs natively so MLX can use the Mac's GPU.
Both reuse the models already installed in this checkout.
An optional **Photo to video** workspace animates uploaded or generated images
with LTX-2.3 Q4 on native MLX and saves playable MP4 clips. Install it with
`make setup-video` after installing FFmpeg. See [video setup and usage](studio/README.md#photo-to-video).
The launcher also installs a verified 5 MB Real-ESRGAN upscaler. Large outputs
use local AI upscaling after generation by default; 2K/4K refer to the longest saved edge.
The **Resize** menu also offers native output and direct full-size generation,
both without resizing or AI upscaling. Direct generation uses 16-pixel size steps;
large canvases need more time and memory. The chosen mode is saved in each recipe.
Edits preserve the source aspect ratio. The original-size, 2K and 4K paths have
all completed real GPU checks; see [the output-size report](benchmarks/20261005-output-sizes.json).

For a beginner-friendly launch, double-click **`Start Image Studio.command`**.
It starts this project's ComfyUI in the background, reuses it if already running,
and opens the local studio in your default browser. Choose **01 - Create Images**
or **02 - Edit Images** in the Workflows sidebar. Both are saved on disk.
The editing workflow includes a sample photo and a normal image upload button.
See [START HERE.txt](START%20HERE.txt) for the short guide.

Both beginner presets use Klein 9B 8-bit with SNOFS v1.4, four steps and
guidance 1.0. Create Images saves 1024 × 1024 results to `outputs/create-images/`;
Edit Images saves 768 × 768 results to `outputs/edit-images/`. For editing,
upload a photo, describe the change and click Run. The source file stays intact.
AI edits can also alter the framing or other details, so inspect each result.
The verified sample edit took about five minutes on this Mac.

The environment and weights are installed in this checkout. Start ComfyUI:

```bash
make start
```

Open **http://127.0.0.1:8188**. Open
`workflows/flux2-klein-9b-8bit.json` in ComfyUI, change the prompt, and click Run.
It is also available in the saved workflow list. The Generate node saves images
under `outputs/flux2-klein-9b-8bit/`; PNGs embed the prompt and workflow, so you can
drag a generated PNG onto ComfyUI to restore it.

Generate from the terminal while ComfyUI is running:

```bash
.venv/bin/python scripts/generate.py --prompt "A cinematic photograph of a red fox in fresh snow" --seed 123
```

The command checks the server result, output dimensions, nonblank pixels and PNG
metadata, then writes a timing report under `benchmarks/`. Use `make benchmark`
to run two generations with different seeds. Each image is recomputed.

For the faster 4-bit preset, which is also downloaded:

```bash
.venv/bin/python scripts/generate.py --model flux2-klein-9b-4bit
```

Use the matching saved workflow in the UI. Its initial benchmarks were 42–46 s
at 1024 × 1024. Resolution changes affect both time and memory; keep one image
per generation for the measured performance profile.

## SNOFS adapter

**SNOFS v1.4 for Klein 9B** is installed as a separate 1.09 GB LoKr adapter from
[the author's repository](https://huggingface.co/Ashen3/SNOFS/tree/main/Klein9b).
The workflows reuse the existing 8-bit and 4-bit Klein 9B model directories.
Open `workflows/flux2-klein-9b-8bit-snofs.json` in ComfyUI, or select the matching
saved workflow. The 4-bit workflow is `workflows/flux2-klein-9b-4bit-snofs.json`.
The presets use four steps, guidance 1.0 and adapter strength 1.0. Change strength
in the loader's `loras` field: `SNOFS/klein_snofs_v1_4.safetensors:0.75`.

Generate from the terminal:

```bash
.venv/bin/python scripts/generate.py --lora snofs-klein-9b-v1.4 --prompt "An adult woman wearing a navy coat on a street in Rome"
# Optional: --model flux2-klein-9b-4bit --lora-strength 0.75
```

Images are saved under `outputs/flux2-klein-9b-8bit-snofs/` or the corresponding
4-bit directory. Adapters are applied during inference with `bake_lora=False`.
The adapter remains separate from the quantized base checkpoint.

To reinstall SNOFS on another checkout, after runtime/model setup:

```bash
source scripts/env.sh
.venv/bin/python scripts/download_loras.py snofs-klein-9b-v1.4
```

The downloader pins the author's revision, verifies file size and SHA256, saves
the source and license under `models/loras/`, and creates a relative symlink in
ComfyUI's `models/loras/SNOFS/` directory. Restart ComfyUI after applying runtime
patches. `config/loras.json` records the adapter source and supported model.
SNOFS uses its author's personal-use license; the full terms are downloaded
alongside the adapter. The upstream Klein 9B license also applies.

The 8-bit preset was verified at 1024 × 1024 with a fully clothed adult portrait:
all 336 adapter tensors matched, generation completed in **193.78 s**, and the
PNG contains matching prompt/workflow metadata. Peak MLX allocation was
**13.36 GB**. This run exceeded the base preset's 90-second target. The 4-bit
SNOFS preset was later verified at 512 × 512 through the website: two creations
took about 21 and 19 seconds, and an edit about 30 seconds. See
[the website report](benchmarks/20261005-web-studio.json).
See [the measured run](benchmarks/20261004T234742Z-snofs-8bit.json).

## Ubuntu / NVIDIA laptop

Use a separate checkout on Ubuntu. The CUDA installer keeps Klein 9B, four steps,
guidance 1.0 and the same SNOFS adapter. Fast uses GGUF Q4_K_M; Balanced and High
use GGUF Q8_0. Qwen3 8B uses Q8, with component unloading, CPU offloading and tiled
VAE processing for a 6 GB GPU. Different quantizers and GPU kernels can produce
different images for the same seed; these are the same model family and presets,
not pixel-identical Mac outputs. Recipes record the actual CUDA sources.
The image engine reserves 2 GB for activations and other GPU use, trading some
speed for room during larger edits with multiple references. If sampling still
runs out of GPU memory, it clears unused weights and retries once with full
weight offloading and a 3 GB reserve. The model, seed, steps and output size stay
the same; the retry can take longer. A second failure remains visible for retry.

Install an NVIDIA driver, Python 3.12 with venv, Git, a C++ compiler, curl, FFmpeg
and Docker Desktop. In the new checkout:

```bash
bash scripts/setup_linux.sh
# Open Docker Desktop, then:
bash scripts/start_linux_docker.sh 192.168.1.135
```

The **local-image-studio-gf65** app appears in Docker Desktop's Containers view.
Use its Start/Stop buttons after this first setup. The website and persistent
library run in Docker; the user systemd helper runs ComfyUI on the host's NVIDIA
GPU, wakes it for jobs, and frees it after ten idle minutes. Docker Desktop for
Linux [does not expose NVIDIA GPUs to ordinary containers](https://docs.docker.com/desktop/features/gpu/). This follows the Mac
deployment arrangement. The laptop must be logged in, awake and on the home Wi-Fi.
The helper is enabled at login; Docker Desktop starts at login when its setting
is enabled. Run the launcher again if the laptop's IP changes.

The launcher prints the PIN and phone setup address. Browser access is
`http://192.168.1.135:7878` for certificate setup, followed by
`https://192.168.1.135:7443`. Certificates, PIN and library are separate from the
Mac installation. Automatic selection uses the same SAM 3 Q8 weights on CPU;
its timeout is longer because that part can be slower.

Optional photo-to-video uses LTX-2.3 distilled 1.1 Q4, the matching Gemma 3 12B
encoder, audio/video VAEs and the two-stage spatial upscaler. Install its pinned,
checksum-verified CUDA weights with:

```bash
.venv/bin/python scripts/setup_video_cuda.py
```

Video runs in a separate process after the helper releases the image engine.
It uses RAM and swap as well as the GPU. The saved recipe records each component.
Model files remain under `models/cuda`; downloaded runtimes and the Linux virtual
environment remain local. Source deployment should exclude `.venv`, `.cache`,
`models`, `runtime`, `outputs` and `studio-data` when updating an existing laptop.
See the [GF65 validation report](benchmarks/20261030-gf65-cuda.json) for measured
image/video timings and the verified Docker deployment.

## Install on another Mac

Requires Apple Silicon, macOS compatible with MLX 0.32.3, Git, and
[uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv`).

```bash
git clone <your-repository-url> LocalImageModels
cd LocalImageModels
./scripts/setup.sh
make start
```

Setup installs project-local Python **3.12.14**, an isolated `.venv`, pinned
ComfyUI and MLX nodes, the exact dependencies in `requirements.lock.txt`, and the
pinned 8-bit model revision. The 8-bit model download is approximately **17.9 GB**.
Use `./scripts/setup.sh --no-models` to install only the runtime.

To download an optional preset:

```bash
source scripts/env.sh
.venv/bin/python scripts/download_models.py flux2-klein-9b-4bit
# Apache-2.0 alternative, not benchmarked in this checkout:
.venv/bin/python scripts/download_models.py flux2-klein-4b-8bit
```

Python, dependency caches, model files and application state stay inside this
checkout. Git ignores weights, runtime clones, caches and generated outputs.
The example image and benchmark reports are retained in the repository.

## Why this setup

[BFL's model lineup](https://github.com/black-forest-labs/flux2) identifies Klein
9B as the high-quality text-to-image option in the fast Klein family. Its
4-step distilled variant is a practical candidate for this Mac's 90-second
budget. Native MLX quantization avoids loading the larger BF16 checkpoint and
uses the Apple GPU through the
[ComfyUI FLUX Klein MLX extension](https://github.com/tanis2000/comfyui-flux2klein-mlx).

A local patch enables **MFLUX's built-in MemorySaver**: the text encoder is
released after encoding, and the transformer is released before VAE decoding.
Each request loads a fresh pipeline so subsequent prompts work after unloading.
The patch also limits the MLX allocator cache to 1 GB and connects sampling
progress and cancellation to ComfyUI. FLUX's VAE remains untiled in this setup.

The original integration took 184.16 s for the same 8-bit image. With component
unloading it took 75.66 s, and the decoded RGB pixels for the same prompt and
seed were **identical**. This keeps the full 8-bit quality preset within the
measured time budget. See [benchmark details](benchmarks/README.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `config/models.json` | Model IDs, exact source revisions and licenses |
| `config/loras.json` | Adapter sources, checksums and compatible models |
| `config/upscaler.json` | Pinned AI upscaler source, checksum and scale |
| `config/defaults.json` | Default model and image settings |
| `config/runtime.json` | Exact ComfyUI and extension commits |
| `requirements.lock.txt` | Exact tested Python package versions |
| `patches/` | Reproducible Apple memory and progress integration |
| `scripts/` | Setup, model downloads, server and generation commands |
| `workflows/` | Portable ComfyUI workflows |
| `models/` | Shared local model library, ignored by Git |
| `runtime/` | Upstream repositories and local ComfyUI state, ignored by Git |
| `outputs/` | Generated PNGs, ignored by Git |
| `studio/` | Phone-friendly website, API and tests |
| `studio-data/` | Persistent website library, sources and PIN, ignored by Git |
| `compose.yaml` | Docker website connected to the native Mac engine |
| `benchmarks/` | Measured performance and validation evidence |

To add another family, add its pinned source to the model registry, its ComfyUI
integration to the runtime setup, and a workflow. Keep large weights in
`models/`, then benchmark on the target Mac before changing the default.

## Validation and license

```bash
make check
make test-studio
```

Checks Python and shell syntax, installed dependency compatibility and diff
whitespace. The benchmark command provides the real GPU integration check.
The studio tests cover PIN access, variations, archive/restore persistence,
editing sources, exact/2K/4K output dimensions, complete upscaling workflows,
targeted cancellation and recovery without duplicate jobs.
Deletion checks cover confirmation, access control, image/recipe/file removal,
preserved editing sources, and recovery after interrupted deletion.
Text-to-image and image editing with a normal Load Image input are verified.
The editing check uses the saved beginner workflow and SNOFS, validates PNG
metadata and visually checks the requested coat-color change. See
[validation details](benchmarks/README.md).

**Klein 9B weights use BFL's FLUX Non-Commercial License.** The Apache tag on the
community quantization card does not replace the upstream model license. Review
the [upstream model terms](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B)
for your use. Klein 4B weights are Apache 2.0.

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
Fast / Balanced / High quality, favorite results, archive and restore images,
and reuse the settings saved with every image. The library persists in
`studio-data/`. See [the website guide](studio/README.md).

The website runs in Docker; ComfyUI runs natively so MLX can use the Mac's GPU.
Both reuse the models already installed in this checkout.

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
editing sources, targeted cancellation and recovery without duplicate jobs.
Text-to-image and image editing with a normal Load Image input are verified.
The editing check uses the saved beginner workflow and SNOFS, validates PNG
metadata and visually checks the requested coat-color change. See
[validation details](benchmarks/README.md).

**Klein 9B weights use BFL's FLUX Non-Commercial License.** The Apache tag on the
community quantization card does not replace the upstream model license. Review
the [upstream model terms](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B)
for your use. Klein 4B weights are Apache 2.0.

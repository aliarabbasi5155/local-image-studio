# Models and licenses

Local Image Studio's own code is licensed under [Apache-2.0](../LICENSE). The models it downloads are
not part of this repository and keep their own licenses, which also govern how you may use them.
**Read the model licenses before any commercial use.**

Every download is pinned to an exact revision and verified by size and SHA-256. The pins are in
`config/`.

## Image models

| Model | Used for | Download | License |
| --- | --- | --- | --- |
| FLUX.2 Klein 9B, MLX 8-bit and 4-bit ([mlx-community](https://huggingface.co/mlx-community/flux2-klein-9b-8bit)) | Creating and editing on macOS | 17.9 + 9.5 GB | [FLUX Non-Commercial License](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B/blob/main/LICENSE.md) |
| FLUX.2 Klein 9B, GGUF Q8_0 and Q4_K_M ([unsloth](https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF)) | Creating and editing on NVIDIA | 10.2 + 5.7 GB | FLUX Non-Commercial License |
| FLUX.2 VAE ([Comfy-Org](https://huggingface.co/Comfy-Org/flux2-klein-9B)) | NVIDIA | 0.3 GB | FLUX Non-Commercial License |
| Qwen3 8B, GGUF Q8_0 ([unsloth](https://huggingface.co/unsloth/Qwen3-8B-GGUF)) | Text encoder on NVIDIA | 8.7 GB | Apache-2.0 |
| SAM 3, GGML Q8_0 ([sam3.cpp](https://huggingface.co/PABannier/sam3.cpp)) | Automatic selection | 1.1 GB | Meta's [SAM 3](https://github.com/facebookresearch/sam3) license |
| Real-ESRGAN General x4v3 ([release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0)) | 2K/4K upscaling | 5 MB | BSD-3-Clause |

The FLUX.2 Klein 9B license is non-commercial. The community quantizations above carry their own
repository tags, but those tags don't replace the upstream model's license.

[FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) is Apache-2.0. Its MLX
8-bit build is pinned as `flux2-klein-4b-8bit` for the [command line and ComfyUI](development.md#command-line-and-comfyui),
but the studio's presets use 9B, which was the model tested for quality and speed.

## Optional: photo to video

| Model | Platform | License |
| --- | --- | --- |
| LTX-2.3 distilled 1.1, MLX Q4 ([dgrauet](https://huggingface.co/dgrauet/ltx-2.3-mlx-q4)) with the [ltx-2-mlx](https://github.com/dgrauet/ltx-2-mlx) runtime | macOS | [LTX-2 Community License](https://github.com/Lightricks/LTX-2/blob/main/LICENSE) |
| Gemma 3 12B, 4-bit ([mlx-community](https://huggingface.co/mlx-community/gemma-3-12b-it-4bit)) | macOS | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) |
| LTX-2.3 distilled 1.1, GGUF Q4_K_M ([unsloth](https://huggingface.co/unsloth/LTX-2.3-GGUF)) | NVIDIA | LTX-2 Community License |
| Gemma 3 12B, GGUF Q4_K_M ([unsloth](https://huggingface.co/unsloth/gemma-3-12b-it-GGUF)) | NVIDIA | Gemma Terms of Use |

## SNOFS adapter

[SNOFS](https://huggingface.co/Ashen3/SNOFS) v1.4 for Klein 9B is an optional third-party style
adapter (a LoKr, 1.1 GB). It is **not installed by default**. Its author's *Model Personal Use License*
allows local use on your own hardware and use of the images you make. It prohibits offering the model
to others as a service (including a free website or bot), creating derivative models from it, and
redistributing its weights. Read the full terms before installing; the license file is downloaded
next to the adapter in `models/loras/`.

To install it: `./install.sh --with-snofs` (source installs) or `LIS_SNOFS=1` in `.env` (Docker). It
is then a toggle in **Settings → Style**.

## Software installed by the setup

| Software | License |
| --- | --- |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 |
| [comfyui-flux2klein-mlx](https://github.com/tanis2000/comfyui-flux2klein-mlx), patched by `patches/` | MIT |
| [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) | Apache-2.0 |
| [MFLUX](https://github.com/filipstrand/mflux) and [MLX](https://github.com/ml-explore/mlx) | MIT |
| [sam3.cpp](https://github.com/PABannier/sam3.cpp) | MIT |
| [PyTorch](https://github.com/pytorch/pytorch) | BSD-3-Clause |

These are downloaded from their sources during installation (or into the Docker engine image you build
yourself), at the commits pinned in `config/runtime.json`, `config/cuda.json` and `config/sam3.json`.

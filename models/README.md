# Model library

Downloaded weights live here and are never committed. The installer fills this folder; exact sources,
revisions, sizes and checksums are pinned in `config/`:

| Folder | Contents | Pinned in |
| --- | --- | --- |
| `flux2-klein-9b-8bit/`, `flux2-klein-9b-4bit/` | FLUX.2 Klein 9B for Apple MLX (macOS) | `config/models.json` |
| `cuda/` | FLUX.2 Klein 9B GGUF, Qwen3 8B text encoder and VAE (NVIDIA) | `config/cuda.json` |
| `sam3/` | SAM 3 for automatic selection | `config/sam3.json` |
| `upscale/` | Real-ESRGAN upscaler | `config/upscaler.json` |
| `loras/` | Optional adapters, such as SNOFS | `config/loras.json` |
| `ltx-2.3-*`, `gemma-*` | Optional photo-to-video | `config/video*.json` |

Each model keeps its own license; see [docs/models-and-licenses.md](../docs/models-and-licenses.md).

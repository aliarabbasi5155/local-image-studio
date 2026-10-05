Downloaded weights live here, outside Git. Exact source revisions are in
`config/models.json`. `scripts/download_models.py` downloads weights, tokenizers
and VAEs together in the MLX/MFLUX format. Future model families can share this
directory without changing the ComfyUI installation.

Qwen editing assets live in `models/qwen/<asset-key>/`. The two GGUF bases,
shared vision/text encoder, VAE, Lightning and alternative editing adapters
are pinned with sizes and SHA256 hashes in `config/qwen.json`. Double-click
`Add Qwen Models.command` to install or resume them. Relative links register
the assets under `Qwen/` in ComfyUI's model directories; the files remain
outside Git and Docker. The eight assets occupy about 33.4 GB in total.

Adapters live in `models/loras/<adapter-key>/`. Their pinned sources, file sizes
and SHA256 checksums are in `config/loras.json`. Run
`scripts/download_loras.py snofs-klein-9b-v1.4` with the project Python to install
SNOFS v1.4 and its license. The downloader registers a relative symlink in
ComfyUI's LoRA directory; workflows use `SNOFS/klein_snofs_v1_4.safetensors`.

FLUX.2 Klein **9B** upstream weights use the **FLUX Non-Commercial License**.
The Apache tag on the community quantization cards does not replace the upstream
license. FLUX.2 Klein **4B** uses Apache 2.0.

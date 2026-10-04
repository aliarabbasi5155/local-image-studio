Downloaded weights live here, outside Git. Exact source revisions are in
`config/models.json`. `scripts/download_models.py` downloads weights, tokenizers
and VAEs together in the MLX/MFLUX format. Future model families can share this
directory without changing the ComfyUI installation.

FLUX.2 Klein **9B** upstream weights use the **FLUX Non-Commercial License**.
The Apache tag on the community quantization cards does not replace the upstream
license. FLUX.2 Klein **4B** uses Apache 2.0.

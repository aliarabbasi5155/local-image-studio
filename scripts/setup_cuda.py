#!/usr/bin/env python3
"""Install the separate, pinned CUDA backend without modifying the Mac runtime."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from setup_runtime import install

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/cuda.json").read_text())


def download(source, category):
    from huggingface_hub import hf_hub_download
    target = ROOT / "models/cuda" / category
    path = Path(hf_hub_download(source["repo_id"], source["filename"],
                               revision=source["revision"], local_dir=target))
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != source["size"] or digest != source["sha256"]:
        raise RuntimeError(f"Model checksum mismatch: {path}")
    link = ROOT / "runtime/ComfyUI/models" / category / path.name
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(os.path.relpath(path, link.parent))
    elif link.resolve() != path.resolve():
        raise FileExistsError(link)
    print(f"Verified {path.name}: {digest}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-models", action="store_true")
    args = parser.parse_args()
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("Install CUDA-enabled PyTorch and a working NVIDIA driver first.")
    comfy = ROOT / "runtime/ComfyUI"
    install(json.loads((ROOT / "config/runtime.json").read_text())["comfyui"], comfy)
    if (comfy / "custom_nodes/comfyui-flux2klein-mlx").exists():
        raise RuntimeError("Use a separate checkout for CUDA; the MLX nodes have the same workflow IDs.")
    install(CONFIG["gguf_runtime"], comfy / "custom_nodes/ComfyUI-GGUF")
    plugin = comfy / "custom_nodes/studio-klein-cuda"
    plugin.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "scripts/cuda_nodes.py", plugin / "__init__.py")
    for directory in ("outputs", "runtime/input", "runtime/user/default/workflows"):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "workflows").glob("*.json"):
        destination = ROOT / "runtime/user/default/workflows" / source.name
        if not destination.exists():
            shutil.copy2(source, destination)
    (ROOT / "runtime/backend.txt").write_text("cuda\n")
    if not args.no_models:
        # Serial downloads keep memory and disk pressure bounded on a 16 GB laptop.
        download(CONFIG["text_encoder"], "text_encoders")
        download(CONFIG["vae"], "vae")
        for source in CONFIG["models"].values():
            download(source, "diffusion_models")
    print("CUDA backend installed.", flush=True)


if __name__ == "__main__":
    main()

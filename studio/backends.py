"""Resolve installed model sources for native and container entry points."""
import json
import os
from pathlib import Path


def is_cuda(root):
    marker = Path(root) / "runtime/backend.txt"
    return os.environ.get("STUDIO_BACKEND") == "cuda" or (marker.exists() and marker.read_text().strip() == "cuda")


def model_registry(root):
    root = Path(root)
    models = json.loads((root / "config/models.json").read_text())
    if is_cuda(root):
        cuda = json.loads((root / "config/cuda.json").read_text())
        # This backend has no 4B checkpoint; don't offer an uninstalled preset.
        models = {key: value for key, value in models.items() if value["model_name"] == "flux2-klein-9b"}
        for bits in ("4", "8"):
            key = f"flux2-klein-9b-{bits}bit"
            models[key] = {**models[key], **cuda["models"][bits], "backend": "cuda-gguf",
                           "download_bytes": cuda["models"][bits]["size"], "text_encoder": cuda["text_encoder"],
                           "vae": cuda["vae"], "vae_tiling": {"tile_size": 512, "overlap": 64}}
    return models


def runtime_registry(root):
    root = Path(root)
    runtime = json.loads((root / "config/runtime.json").read_text())
    if is_cuda(root):
        cuda = json.loads((root / "config/cuda.json").read_text())
        return {"comfyui": runtime["comfyui"], "gguf": cuda["gguf_runtime"], "backend": "cuda"}
    return runtime

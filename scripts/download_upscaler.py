#!/usr/bin/env python3
"""Install the pinned official Real-ESRGAN model for the web studio."""
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def install():
    model = json.loads((ROOT / "config/upscaler.json").read_text())
    target = ROOT / "models/upscale" / model["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        print("Downloading the local AI upscaler (about 5 MB)…", flush=True)
        temporary = target.with_suffix(".part")
        try:
            with urlopen(model["url"], timeout=60) as response, temporary.open("wb") as file:
                while chunk := response.read(1024 * 1024):
                    file.write(chunk)
            verify(temporary, model)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    verify(target, model)
    link = ROOT / "runtime/ComfyUI/models/upscale_models" / model["filename"]
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if link.resolve() != target.resolve():
            raise FileExistsError(f"Keep the existing upscaler at {link}; move it aside before installing this version.")
    else:
        link.symlink_to(os.path.relpath(target, link.parent))
    (target.parent / "source.json").write_text(json.dumps(model, indent=2) + "\n")
    print("Local AI upscaler is ready.", flush=True)


def verify(path, model):
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if path.stat().st_size != model["download_bytes"] or digest != model["sha256"]:
        raise ValueError(f"The upscaler checksum does not match: {path}")


if __name__ == "__main__":
    install()

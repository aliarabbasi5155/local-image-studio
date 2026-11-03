#!/usr/bin/env python3
"""Install pinned adapters in the local model library and register them in ComfyUI."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import hf_hub_download
from safetensors import safe_open

ROOT = Path(__file__).resolve().parents[1]


def download(key, adapters):
    adapter = adapters[key]
    target = ROOT / "models" / "loras" / key
    path = target / adapter["filename"]
    link = ROOT / "runtime" / "ComfyUI" / "models" / "loras" / adapter["comfy_filename"]
    source = target / "source.json"
    # source.json is written only after verification, so a match means this exact file was checked.
    if (source.is_file() and json.loads(source.read_text()) == adapter and path.is_file()
            and path.stat().st_size == adapter["download_bytes"] and link.resolve() == path.resolve()):
        print(f"Ready: {key} ({adapter['comfy_filename']})", flush=True)
        return
    print(f"Downloading {adapter['repo_id']} / {adapter['filename']} @ {adapter['revision']}", flush=True)
    path = Path(hf_hub_download(
        repo_id=adapter["repo_id"], revision=adapter["revision"],
        filename=adapter["filename"], local_dir=target,
    ))
    if path.stat().st_size != adapter["download_bytes"]:
        raise ValueError(f"Unexpected adapter size: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != adapter["sha256"]:
        raise ValueError(f"Adapter SHA256 mismatch: {path}")
    with safe_open(path, framework="np") as weights:
        if not list(weights.keys()):
            raise ValueError(f"Empty adapter: {path}")
    for filename in ("README.md", "LICENSE"):
        hf_hub_download(repo_id=adapter["repo_id"], revision=adapter["revision"],
                        filename=filename, local_dir=target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if link.resolve() != path.resolve():
            raise FileExistsError(f"Keep existing adapter at {link}; move it aside before installing this version.")
    else:
        link.symlink_to(os.path.relpath(path, link.parent))
    (target / "source.json").write_text(json.dumps(adapter, indent=2) + "\n")
    print(f"Verified SHA256: {digest}\nReady: {key} ({adapter['comfy_filename']})", flush=True)


def main():
    adapters = json.loads((ROOT / "config/loras.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("loras", nargs="+", choices=list(adapters))
    args = parser.parse_args()
    for key in args.loras:
        download(key, adapters)


if __name__ == "__main__":
    main()

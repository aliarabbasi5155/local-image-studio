#!/usr/bin/env python3
"""Install pinned Qwen edit assets and GGUF nodes for this local Mac studio."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from huggingface_hub import hf_hub_download

from setup_runtime import install

ROOT = Path(__file__).resolve().parents[1]


def apply_patch(checkout, filename):
    patch = ROOT / "patches" / filename
    options = ["--ignore-whitespace"]
    reverse = subprocess.run(["git", "-C", str(checkout), "apply", "--reverse", "--check", *options, str(patch)], capture_output=True)
    if reverse.returncode:
        subprocess.run(["git", "-C", str(checkout), "apply", "--check", *options, str(patch)], check=True)
        subprocess.run(["git", "-C", str(checkout), "apply", *options, str(patch)], check=True)


def download(key, asset):
    target = ROOT / "models/qwen" / key
    print(f"Installing {key}: {asset['filename']}", flush=True)
    path = Path(hf_hub_download(repo_id=asset["repo_id"], revision=asset["revision"],
                              filename=asset["filename"], local_dir=target))
    if path.stat().st_size != asset["download_bytes"]:
        raise ValueError(f"Unexpected file size: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != asset["sha256"]:
        raise ValueError(f"SHA256 mismatch: {path}")
    link = ROOT / "runtime/ComfyUI/models" / asset["folder"] / asset["comfy_filename"]
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        if link.resolve() != path.resolve():
            raise FileExistsError(f"An existing model occupies {link}; keep it and move it aside first.")
    else:
        link.symlink_to(os.path.relpath(path, link.parent))
    (target / "source.json").write_text(json.dumps(asset, indent=2) + "\n")
    print(f"Verified {key}: SHA256 {digest}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes-only", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / "config/qwen.json").read_text())
    for patch in config["local_patches"]:
        if hashlib.sha256((ROOT / patch["file"]).read_bytes()).hexdigest() != patch["sha256"]:
            raise ValueError(f"Unexpected patch contents: {patch['file']}")
    install(config["gguf_node"], ROOT / "runtime/ComfyUI/custom_nodes/ComfyUI-GGUF")
    plugin = ROOT / "runtime/ComfyUI/custom_nodes/ComfyUI-GGUF"
    apply_patch(plugin, "qwen-gguf-memory.patch")
    apply_patch(ROOT / "runtime/ComfyUI", "qwen-reference-resolution.patch")
    subprocess.run(["uv", "pip", "install", "--python", str(ROOT / ".venv/bin/python"),
                    "gguf==0.19.0"], check=True, env=dict(os.environ, UV_CACHE_DIR=str(ROOT / ".cache/uv")))
    if args.nodes_only:
        return
    needed = sum(a["download_bytes"] for key, a in config["assets"].items()
                 if not (ROOT / "models/qwen" / key / a["filename"]).exists())
    if shutil.disk_usage(ROOT).free < needed + 5 * 1024**3:
        raise SystemExit("Not enough free disk space for the Qwen models plus 5 GB of working space.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: download(*item), config["assets"].items()))
    destination = ROOT / "runtime/user/default/workflows"
    destination.mkdir(parents=True, exist_ok=True)
    for workflow in (ROOT / "workflows").glob("*Qwen*.json"):
        if not (destination / workflow.name).exists():
            shutil.copy2(workflow, destination / workflow.name)
    sample = ROOT / "runtime/input/qwen-lakeside-source.png"
    if not sample.exists():
        sample.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples/flux2-klein-9b-8bit.png", sample)
    (ROOT / "runtime/qwen-installed.json").write_text(json.dumps(config, indent=2) + "\n")
    print("Qwen assets installed. Restart ComfyUI when its queue is empty, then refresh Image Studio.")


if __name__ == "__main__":
    main()

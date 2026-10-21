#!/usr/bin/env python3
"""Install an isolated, pinned MLX video runtime and download its model files."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Run video setup natively on an Apple Silicon Mac.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("Install the MP4 encoder first: brew install ffmpeg")
    config = json.loads((ROOT / "config/video.json").read_text())
    runtime = ROOT / "runtime/ltx-2-mlx"
    if not runtime.exists():
        subprocess.run(["git", "clone", config["runtime_repository"], str(runtime)], check=True)
    revision = subprocess.check_output(["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True).strip()
    if revision != config["runtime_revision"]:
        if subprocess.check_output(["git", "-C", str(runtime), "status", "--porcelain"], text=True).strip():
            raise RuntimeError("The video runtime has local changes; preserve them before updating it.")
        subprocess.run(["git", "-C", str(runtime), "fetch", "origin", config["runtime_revision"]], check=True)
        subprocess.run(["git", "-C", str(runtime), "checkout", "--detach", config["runtime_revision"]], check=True)
    env = dict(os.environ, UV_CACHE_DIR=str(ROOT / ".cache/uv"), HF_HOME=str(ROOT / ".cache/huggingface"))
    subprocess.run(["uv", "sync", "--project", str(runtime), "--frozen", "--no-dev", "--python", sys.executable], env=env, check=True)
    if args.runtime_only:
        return
    # Use the runtime's hub client; never change the image engine's dependencies.
    subprocess.run([str(runtime / ".venv/bin/python"), "-c", '''
import json, sys
from pathlib import Path
from huggingface_hub import snapshot_download
root = Path(sys.argv[1])
config = json.loads((root / "config/video.json").read_text())
for key in ("model", "text_encoder"):
    spec = config[key]
    target = root / "models" / spec["directory"]
    print("Downloading", spec["repo_id"], "at", spec["revision"], flush=True)
    snapshot_download(spec["repo_id"], revision=spec["revision"], local_dir=target,
                      allow_patterns=spec.get("files"))
    (target / "source.json").write_text(json.dumps(spec, indent=2) + "\\n")
(root / "runtime/video-ready.json").write_text(json.dumps(config, indent=2) + "\\n")
''', str(ROOT)], env=env, check=True)
    print("Video runtime and weights ready. Restart the Mac helper and Studio after current jobs finish.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Install the same LTX 2.3 distilled family for the separate CUDA worker."""
import json
from pathlib import Path

from setup_cuda import download

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / "config/video.cuda.json").read_text())
    for key in ("text_encoder", "projection", "video_vae", "audio_vae", "upscaler", "model"):
        source = config[key]
        download(source, source["category"])
    (ROOT / "runtime/video-cuda-ready.json").write_text(json.dumps(config, indent=2) + "\n")
    print("CUDA video models verified.", flush=True)


if __name__ == "__main__":
    main()

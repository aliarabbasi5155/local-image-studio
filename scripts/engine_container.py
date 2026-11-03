#!/usr/bin/env python3
"""Prepare the Docker GPU engine on every start: the helper token, the phone certificate and verified models.

The first start downloads about 26 GB into ./models; later starts only check the verified stamps.
"""
import json
import os
from pathlib import Path

from download_loras import download as download_adapter
from download_upscaler import install as install_upscaler
from home_certificate import ensure as ensure_certificate
from setup_cuda import download_models
from setup_sam3 import create_token, download_model as download_sam3

ROOT = Path(__file__).resolve().parents[1]


def enabled(name):
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def main():
    state = Path(os.environ.get("LIS_STATE", ROOT / "runtime"))
    create_token(Path(os.environ["SAM3_TOKEN_FILE"]))
    address = os.environ.get("LIS_ADDRESS", "").strip()
    if address:
        # Only the tls/ folder reaches the website container; the authority key stays in home-ca/.
        ensure_certificate(address, None, state)
    else:
        print("LIS_ADDRESS is not set, so the installable phone app is off. The website works on http://<this computer>:7878.", flush=True)
    print("Checking models (the first start downloads about 26 GB)…", flush=True)
    download_models()
    install_upscaler()
    download_sam3()
    if enabled("LIS_SNOFS"):
        download_adapter("snofs-klein-9b-v1.4", json.loads((ROOT / "config/loras.json").read_text()))
    if enabled("LIS_VIDEO"):
        from setup_video_cuda import main as setup_video
        setup_video()
    print("Models are ready. Starting the helper.", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download an exact model revision into the shared, Git-ignored model library."""
import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]


def download(key, models):
    model = models[key]
    target = ROOT / "models" / key
    print(f"Downloading {model['repo_id']} @ {model['revision']} -> {target}", flush=True)
    snapshot_download(
        repo_id=model["repo_id"],
        revision=model["revision"],
        local_dir=target,
        allow_patterns=["*.safetensors", "*.json", "*.jinja", "README.md", "LICENSE*"],
        max_workers=4,
    )
    (target / "source.json").write_text(json.dumps(model, indent=2) + "\n")
    print(f"Ready: {key}", flush=True)


def main():
    models = json.loads((ROOT / "config/models.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=list(models))
    args = parser.parse_args()
    for key in args.models:
        download(key, models)


if __name__ == "__main__":
    main()

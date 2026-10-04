#!/usr/bin/env python3
"""Install pinned upstream repositories without overwriting local changes."""
import json
import subprocess
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(directory, *args, check=True):
    return subprocess.run(["git", "-C", str(directory), *args], check=check, text=True, capture_output=True)


def install(source, directory):
    if not directory.exists():
        directory.mkdir(parents=True)
        git(directory, "init")
        git(directory, "remote", "add", "origin", source["repository"])
        git(directory, "fetch", "--depth", "1", "origin", source["revision"])
        git(directory, "checkout", "--detach", "FETCH_HEAD")
    actual = git(directory, "rev-parse", "HEAD").stdout.strip()
    if actual != source["revision"]:
        raise SystemExit(f"{directory}: expected {source['revision']}, found {actual}. Keep local changes and move this runtime aside before reinstalling.")
    print(f"Verified {directory.relative_to(ROOT)} @ {actual}", flush=True)


def main():
    runtime = json.loads((ROOT / "config/runtime.json").read_text())
    comfy = ROOT / "runtime/ComfyUI"
    plugin = comfy / "custom_nodes/comfyui-flux2klein-mlx"
    install(runtime["comfyui"], comfy)
    install(runtime["flux2klein_mlx"], plugin)
    patch = ROOT / "patches/flux2klein-mlx.patch"
    applied = git(plugin, "apply", "--reverse", "--check", str(patch), check=False)
    if applied.returncode != 0:
        git(plugin, "apply", "--check", str(patch))
        git(plugin, "apply", str(patch))
    print("Verified Apple memory settings patch", flush=True)
    saved_workflows = ROOT / "runtime/user/default/workflows"
    saved_workflows.mkdir(parents=True, exist_ok=True)
    for workflow in (ROOT / "workflows").glob("*.json"):
        destination = saved_workflows / workflow.name
        if not destination.exists():
            shutil.copy2(workflow, destination)


if __name__ == "__main__":
    main()

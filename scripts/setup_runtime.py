#!/usr/bin/env python3
"""Install pinned upstream repositories without overwriting local changes."""
import json
import subprocess
import shutil
import tempfile
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
    patches = [ROOT / "patches" / name for name in (
        "flux2klein-mlx.patch", "flux2klein-lora.patch", "flux2klein-edit.patch", "flux2klein-model-handoff.patch",
    )]
    # Check the whole patch stack on a copy before changing installed sources.
    with tempfile.TemporaryDirectory(prefix="flux2klein-setup-") as name:
        staged = Path(name)
        shutil.copytree(plugin / "nodes", staged / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
        for patch in reversed(patches):
            if git(staged, "apply", "--reverse", "--check", str(patch), check=False).returncode == 0:
                git(staged, "apply", "--reverse", str(patch))
        for patch in patches:
            git(staged, "apply", "--check", str(patch))
            git(staged, "apply", str(patch))
            print(f"Verified {patch.name}", flush=True)
        for source in (staged / "nodes").rglob("*.py"):
            destination = plugin / source.relative_to(staged)
            if source.read_bytes() != destination.read_bytes():
                shutil.copy2(source, destination)
    saved_workflows = ROOT / "runtime/user/default/workflows"
    saved_workflows.mkdir(parents=True, exist_ok=True)
    for workflow in (ROOT / "workflows").glob("*.json"):
        destination = saved_workflows / workflow.name
        if not destination.exists():
            shutil.copy2(workflow, destination)
    sample = ROOT / "runtime/input/example-edit-source.png"
    sample.parent.mkdir(parents=True, exist_ok=True)
    if not sample.exists():
        shutil.copy2(ROOT / "examples/edit-source.png", sample)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Install automatic masks (SAM 3) without changing the image environment."""
import argparse
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/sam3.json").read_text())


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def install():
    download_model()
    build()
    create_token(ROOT / "runtime/sam3-token.txt")


def download_model():
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    model = ROOT / "models/sam3" / CONFIG["filename"]
    model.parent.mkdir(parents=True, exist_ok=True)
    verified = runtime / "sam3-model-verified.json"
    stamp = {"sha256": CONFIG["sha256"], "size": CONFIG["size"]}
    if not model.exists() or model.stat().st_size != CONFIG["size"] or not verified.exists() or json.loads(verified.read_text()) != stamp:
        if not model.exists() or model.stat().st_size != CONFIG["size"] or checksum(model) != CONFIG["sha256"]:
            print("Downloading SAM 3 for automatic selection (1.1 GB)…", flush=True)
            partial = model.with_suffix(".part")
            url = f"https://huggingface.co/{CONFIG['model_repository']}/resolve/{CONFIG['model_revision']}/{CONFIG['filename']}"
            subprocess.run(["curl", "-fL", "--retry", "3", "--silent", "--show-error", url, "-o", str(partial)], check=True)
            if partial.stat().st_size != CONFIG["size"] or checksum(partial) != CONFIG["sha256"]:
                partial.unlink(missing_ok=True)
                raise RuntimeError("The automatic mask model failed verification. Try the launcher again.")
            partial.replace(model)
        verified.write_text(json.dumps(stamp))


def build():
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    source = runtime / "sam3.cpp"
    if not source.exists():
        subprocess.run(["git", "clone", CONFIG["repository"], str(source)], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != CONFIG["commit"]:
        subprocess.run(["git", "-C", str(source), "fetch", "origin", CONFIG["commit"]], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", CONFIG["commit"]], check=True)
    subprocess.run(["git", "-C", str(source), "submodule", "update", "--init", "--recursive"], check=True)
    metal = platform.system() == "Darwin"
    # Docker images set SAM3_NATIVE=OFF so the binary runs on any x86-64 CPU, not only the build machine's.
    native = os.environ.get("SAM3_NATIVE", "ON")
    fingerprint = CONFIG["commit"] + checksum(ROOT / "scripts/sam3/main.cpp") + checksum(ROOT / "scripts/sam3/CMakeLists.txt") + str(metal) + \
        ("" if native == "ON" else native)
    binary = runtime / "sam3-build/studio-sam3"
    built = runtime / "sam3-build-fingerprint.txt"
    if not binary.exists() or not built.exists() or built.read_text() != fingerprint:
        print("Building automatic selection…", flush=True)
        environment = runtime / "sam3-build-env"
        uv = shutil.which("uv") or str(ROOT / ".cache/bin/uv")
        if not (environment / "bin/python").exists():
            subprocess.run([uv, "venv", str(environment)], check=True)
        cmake = environment / "bin/cmake"
        if not cmake.exists():
            subprocess.run([uv, "pip", "install", "--python", str(environment / "bin/python"), "cmake==" + CONFIG["cmake"]], check=True)
        subprocess.run([str(cmake), "-S", str(ROOT / "scripts/sam3"), "-B", str(runtime / "sam3-build"),
                        "-DGGML_METAL=" + ("ON" if metal else "OFF"), "-DGGML_NATIVE=" + native, "-DBUILD_SHARED_LIBS=OFF"], check=True)
        subprocess.run([str(cmake), "--build", str(runtime / "sam3-build"), "-j", "4"], check=True)
        built.write_text(fingerprint)


def create_token(token):
    """The shared secret between the website and the helper; every helper request must carry it."""
    token.parent.mkdir(parents=True, exist_ok=True)
    if not token.exists():
        descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_hex(32))
    token.chmod(0o600)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    install()
    print("Automatic selection is ready.")

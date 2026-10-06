#!/usr/bin/env python3
"""Install and start automatic masks without changing the Flux environment."""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/sam3.json").read_text())


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def install():
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
    source = runtime / "sam3.cpp"
    if not source.exists():
        subprocess.run(["git", "clone", CONFIG["repository"], str(source)], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != CONFIG["commit"]:
        subprocess.run(["git", "-C", str(source), "fetch", "origin", CONFIG["commit"]], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", CONFIG["commit"]], check=True)
    subprocess.run(["git", "-C", str(source), "submodule", "update", "--init", "--recursive"], check=True)
    fingerprint = CONFIG["commit"] + checksum(ROOT / "scripts/sam3/main.cpp") + checksum(ROOT / "scripts/sam3/CMakeLists.txt")
    binary = runtime / "sam3-build/studio-sam3"
    built = runtime / "sam3-build-fingerprint.txt"
    if not binary.exists() or not built.exists() or built.read_text() != fingerprint:
        print("Building automatic selection for your Mac…", flush=True)
        environment = runtime / "sam3-build-env"
        uv = shutil.which("uv") or str(ROOT / ".bin/uv")
        if not (environment / "bin/python").exists():
            subprocess.run([uv, "venv", str(environment)], check=True)
        cmake = environment / "bin/cmake"
        if not cmake.exists():
            subprocess.run([uv, "pip", "install", "--python", str(environment / "bin/python"), "cmake==" + CONFIG["cmake"]], check=True)
        subprocess.run([str(cmake), "-S", str(ROOT / "scripts/sam3"), "-B", str(runtime / "sam3-build"),
                        "-DGGML_METAL=ON", "-DGGML_NATIVE=ON", "-DBUILD_SHARED_LIBS=OFF"], check=True)
        subprocess.run([str(cmake), "--build", str(runtime / "sam3-build"), "-j", "4"], check=True)
        built.write_text(fingerprint)
    token = runtime / "sam3-token.txt"
    if not token.exists():
        descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_hex(32))
    token.chmod(0o600)


def start():
    token = (ROOT / "runtime/sam3-token.txt").read_text().strip()
    def ready():
        try:
            with urlopen(Request("http://127.0.0.1:7877/health", headers={"Authorization": "Bearer " + token}), timeout=2) as response:
                return json.load(response).get("ready", False)
        except OSError:
            return False
    if ready():
        return
    with (ROOT / "runtime/sam3-service.log").open("a") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "scripts/sam3_service.py")], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    (ROOT / "runtime/sam3-service.pid").write_text(str(process.pid))
    deadline = time.monotonic() + 15
    while not ready():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Automatic selection could not start. Check runtime/sam3-service.log. The brush still works.")
        time.sleep(.25)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    install()
    if args.start:
        start()
    print("Automatic selection is ready.")

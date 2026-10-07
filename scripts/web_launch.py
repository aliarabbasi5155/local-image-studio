#!/usr/bin/env python3
"""Start the phone-friendly website and the native Mac image engine."""
import argparse
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

from download_upscaler import install as install_upscaler
from setup_sam3 import install as install_sam3
from setup_native_service import install as install_native_service

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:7878"


def lan_address():
    for interface in ("en0", "en1"):
        result = subprocess.run(["ipconfig", "getifaddr", interface], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return socket.gethostname() + ".local"


def ready():
    try:
        with urlopen(URL + "/api/health", timeout=2) as response:
            payload = json.load(response)
        if payload.get("service") != "local-image-studio":
            raise SystemExit("Port 7878 is used by another application. Close it before starting the studio.")
        return True
    except (OSError, TimeoutError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true", help="Run the website in Python instead of Docker")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    (ROOT / "studio-data").mkdir(exist_ok=True)
    phone_url = f"http://{lan_address()}:7878"
    with (runtime / "web-launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install_upscaler()
        try:
            install_sam3()
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"Automatic selection needs attention: {error}\nYou can still paint masks by hand.", flush=True)
        install_native_service()
        print("Your Mac will start the image engine when needed and stop it after 10 idle minutes.", flush=True)
        if args.native:
            if not ready():
                with (runtime / "web-studio.log").open("a") as log:
                    environment = dict(os.environ, STUDIO_LAN_URL=phone_url, ENGINE_CONTROL_URL="http://127.0.0.1:7877")
                    subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=environment,
                                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        else:
            docker = shutil.which("docker") or "/usr/local/bin/docker"
            check = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
            if check.returncode:
                raise SystemExit("Open Docker Desktop, wait for it to start, then double-click this launcher again.\nTo run without Docker, use ./Start\\ Web\\ Studio.command --native")
            values = {"ENGINE_PROJECT_ROOT": str(ROOT), "STUDIO_UID": str(os.getuid()), "STUDIO_GID": str(os.getgid()), "STUDIO_LAN_URL": phone_url}
            environment_file = runtime / "studio-docker.env"
            environment_file.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
            print("Starting the website in Docker…", flush=True)
            subprocess.run([docker, "compose", "--env-file", str(environment_file), "up", "-d", "--build"], cwd=ROOT, check=True)
        deadline = time.monotonic() + 60
        while not ready():
            if time.monotonic() > deadline:
                raise SystemExit("The website didn’t start. Check docker compose logs or runtime/web-studio.log.")
            time.sleep(1)
    pin = (ROOT / "studio-data/access-pin.txt").read_text().strip()
    print(f"\nImage Studio is ready\nOn this Mac: {URL}\nOn your phone (same Wi-Fi): {phone_url}\nYour PIN: {pin}\n", flush=True)
    print("You can close this Terminal window. Keep your Mac awake while making images.", flush=True)
    if not args.no_browser:
        webbrowser.open(URL)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Start the project-local ComfyUI server and open it in the default browser."""
import argparse
import fcntl
import json
import subprocess
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8188"


def server_ready():
    try:
        with urlopen(URL + "/system_stats", timeout=2) as response:
            stats = json.load(response)
    except (URLError, TimeoutError):
        return False
    if str(ROOT / "outputs") not in stats["system"]["argv"]:
        raise SystemExit("Port 8188 is being used by another ComfyUI project. Close that server first.")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Check startup without opening a browser")
    args = parser.parse_args()
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    with (runtime / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not server_ready():
            log_path = runtime / "comfyui-launcher.log"
            with log_path.open("a") as log:
                process = subprocess.Popen(
                    [str(ROOT / "scripts/start.sh")], cwd=ROOT,
                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            print("Starting your local image studio...", flush=True)
            deadline = time.monotonic() + 120
            while not server_ready():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"ComfyUI could not start. See {log_path}")
                time.sleep(1)
    if not args.no_browser:
        webbrowser.open(URL)
    print(f"Ready: {URL}\nChoose 01 - Create Images or 02 - Edit Images in Workflows.")
    print("You can close this Terminal window; the studio keeps running.")


if __name__ == "__main__":
    main()

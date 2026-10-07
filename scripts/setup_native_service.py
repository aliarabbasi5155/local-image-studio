#!/usr/bin/env python3
"""Install the lightweight Mac helper at login; no image model loads at login."""
import argparse
import json
import os
import plistlib
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

import psutil

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.localimagestudio.native"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def health():
    try:
        token = (ROOT / "runtime/sam3-token.txt").read_text().strip()
        with urlopen(Request("http://127.0.0.1:7877/health", headers={"Authorization": "Bearer " + token}), timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def configuration():
    return {"Label": LABEL, "ProgramArguments": [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/sam3_service.py")],
            "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "EnvironmentVariables": {"ENGINE_IDLE_SECONDS": "600", "PYTHONUNBUFFERED": "1"},
            "StandardOutPath": str(ROOT / "runtime/sam3-service.log"),
            "StandardErrorPath": str(ROOT / "runtime/sam3-service.log")}


def install():
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + LABEL
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0
    content = plistlib.dumps(configuration())
    current = health()
    if loaded and PLIST.exists() and PLIST.read_bytes() == content and current.get("engine_control"):
        return
    if loaded:
        raise RuntimeError("The installed Mac helper needs an update. Finish current images before reinstalling it.")
    if current:
        if current.get("busy"):
            raise RuntimeError("Finish automatic selection before installing the Mac helper.")
        # Migrate the previous detached helper, checking identity before signalling it.
        pid_file = ROOT / "runtime/sam3-service.pid"
        if not pid_file.exists():
            raise RuntimeError("Port 7877 is already in use. Close the other helper first.")
        process = psutil.Process(int(pid_file.read_text()))
        if str(ROOT / "scripts/sam3_service.py") not in process.cmdline():
            raise RuntimeError("Another process is using the saved helper PID; it was left running.")
        process.terminate()
        process.wait(timeout=20)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(content)
    PLIST.chmod(0o644)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    deadline = time.monotonic() + 20
    while not health().get("engine_control"):
        if time.monotonic() > deadline:
            raise RuntimeError("The Mac helper did not start. Check runtime/sam3-service.log.")
        time.sleep(.25)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true", help="Remove automatic startup after finishing current images")
    args = parser.parse_args()
    if args.uninstall:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/" + LABEL], check=False)
        PLIST.unlink(missing_ok=True)
        print("Automatic startup removed.")
    else:
        install()
        print("Mac helper installed. Image generation starts on demand and sleeps after 10 idle minutes.")

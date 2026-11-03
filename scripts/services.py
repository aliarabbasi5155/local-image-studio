#!/usr/bin/env python3
"""Run the helper, and the website unless Docker serves it, as login services (launchd on macOS, systemd on Linux).

No image model loads at login: the helper starts the image engine when a job arrives and stops it after idle time.
"""
import argparse
import json
import os
import platform
import plistlib
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil

ROOT = Path(__file__).resolve().parents[1]
MAC = platform.system() == "Darwin"
HELPER, WEBSITE = "helper", "website"
LABELS = {HELPER: "com.localimagestudio.native", WEBSITE: "com.localimagestudio.web"}
UNITS = {HELPER: "local-image-studio-helper", WEBSITE: "local-image-studio-web"}
LOGS = {HELPER: ROOT / "runtime/sam3-service.log", WEBSITE: ROOT / "runtime/web-studio.log"}


def helper_health():
    try:
        token = (ROOT / "runtime/sam3-token.txt").read_text().strip()
        with urlopen(Request("http://127.0.0.1:7877/health", headers={"Authorization": "Bearer " + token}), timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def engine_busy():
    """Restarting the helper stops its engine, so never do it while ComfyUI has work."""
    try:
        with urlopen("http://127.0.0.1:8188/queue", timeout=3) as response:
            queue = json.load(response)
        return bool(queue.get("queue_running") or queue.get("queue_pending"))
    except (URLError, OSError, ValueError):
        return False


def website_environment(lan_url):
    return {"STUDIO_LAN_URL": lan_url, "ENGINE_CONTROL_URL": "http://127.0.0.1:7877",
            "STUDIO_TLS_DIR": str(ROOT / "runtime/tls"), "PYTHONUNBUFFERED": "1",
            **({} if MAC else {"SAM3_TIMEOUT": "900"})}


def helper_environment():
    if MAC:
        return {"ENGINE_IDLE_SECONDS": "600", "PYTHONUNBUFFERED": "1"}
    return {"PATH": f"{ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin", "HF_HOME": f"{ROOT}/.cache/huggingface",
            "HF_HUB_OFFLINE": "1", "ENGINE_IDLE_SECONDS": "600", "PYTHONUNBUFFERED": "1", "SAM3_TIMEOUT": "900"}


def command(name):
    python = str(ROOT / ".venv/bin/python")
    return [python, str(ROOT / "scripts/sam3_service.py")] if name == HELPER else [python, "-m", "studio.server"]


# macOS: per-user LaunchAgents.

def plist_path(name):
    return Path.home() / "Library/LaunchAgents" / (LABELS[name] + ".plist")


def plist(name, environment):
    return plistlib.dumps({"Label": LABELS[name], "ProgramArguments": command(name),
                           "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
                           "EnvironmentVariables": environment,
                           "StandardOutPath": str(LOGS[name]), "StandardErrorPath": str(LOGS[name])})


def launchd_loaded(name):
    return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABELS[name]}"], capture_output=True).returncode == 0


def launchd_stop(name):
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABELS[name]}"], capture_output=True)
    deadline = time.monotonic() + 20
    while launchd_loaded(name) and time.monotonic() < deadline:
        time.sleep(.25)


def launchd_install(name, environment):
    path, content = plist_path(name), plist(name, environment)
    loaded = launchd_loaded(name)
    if loaded and path.exists() and path.read_bytes() == content:
        return False
    if loaded:
        launchd_stop(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o644)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
    return True


# Linux: systemd user units.

def unit_path(name):
    return Path.home() / ".config/systemd/user" / (UNITS[name] + ".service")


def unit(name, environment):
    # systemd quotes are not shell quotes; escape its two special path characters.
    quote = lambda value: str(value).replace("%", "%%").replace('"', '\\"')
    lines = "".join(f'Environment="{key}={quote(value)}"\n' for key, value in environment.items())
    program = " ".join(f'"{quote(part)}"' for part in command(name))
    return f"""[Unit]
Description=Local Image Studio {name}
After=network.target

[Service]
Type=simple
WorkingDirectory={quote(ROOT)}
{lines}ExecStart={program}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
"""


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)


def systemd_install(name, environment):
    path, content = unit_path(name), unit(name, environment)
    changed = not path.exists() or path.read_text() != content
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        systemctl("daemon-reload")
    systemctl("enable", UNITS[name])
    systemctl("restart" if changed else "start", UNITS[name])
    return changed


def running(name):
    if MAC:
        return launchd_loaded(name)
    return systemctl("is-active", "--quiet", UNITS[name], check=False).returncode == 0


def stop(name):
    if MAC:
        launchd_stop(name)
    else:
        systemctl("stop", UNITS[name], check=False)


def remove(name):
    if MAC:
        launchd_stop(name)
        plist_path(name).unlink(missing_ok=True)
    else:
        systemctl("disable", "--now", UNITS[name], check=False)
        unit_path(name).unlink(missing_ok=True)
        systemctl("daemon-reload", check=False)


def definition(name, environment):
    return (plist_path(name), plist(name, environment)) if MAC else (unit_path(name), unit(name, environment).encode())


def outdated(name, environment):
    path, content = definition(name, environment)
    return not path.exists() or path.read_bytes() != content


def install(name, environment):
    return (launchd_install if MAC else systemd_install)(name, environment)


def adopt_detached_helper():
    """Stop a helper started by hand before services existed, checking its identity before signalling it."""
    pid_file = ROOT / "runtime/sam3-service.pid"
    if not pid_file.exists():
        raise RuntimeError("Port 7877 is already in use. Close the other helper first.")
    try:
        process = psutil.Process(int(pid_file.read_text()))
    except (psutil.NoSuchProcess, ValueError):
        pid_file.unlink(missing_ok=True)
        return
    if str(ROOT / "scripts/sam3_service.py") not in process.cmdline():
        raise RuntimeError("Another process is using the saved helper PID; it was left running.")
    process.terminate()
    process.wait(timeout=20)
    pid_file.unlink(missing_ok=True)


def install_helper():
    environment = helper_environment()
    current = helper_health()
    if current and not running(HELPER):
        if current.get("busy"):
            raise RuntimeError("Finish automatic selection before installing the helper.")
        adopt_detached_helper()
    elif running(HELPER) and outdated(HELPER, environment) and (current.get("busy") or engine_busy()):
        raise RuntimeError("The helper needs an update. Let current images finish, then run this again.")
    install(HELPER, environment)
    deadline = time.monotonic() + 20
    while not helper_health().get("engine_control"):
        if time.monotonic() > deadline:
            raise RuntimeError(f"The helper did not start. Check {LOGS[HELPER].relative_to(ROOT)}.")
        time.sleep(.25)


def install_website(lan_url):
    install(WEBSITE, website_environment(lan_url))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true", help="Remove the login services; the library and models stay")
    args = parser.parse_args()
    if args.uninstall:
        for name in (WEBSITE, HELPER):
            remove(name)
        print("Login services removed.")
    else:
        install_helper()
        print("Helper installed. Image generation starts on demand and sleeps after 10 idle minutes.")

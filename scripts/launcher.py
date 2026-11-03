#!/usr/bin/env python3
"""Start, stop and check Local Image Studio: the website, its helper and the on-demand image engine."""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from urllib.request import Request, urlopen

import services
from download_upscaler import install as install_upscaler
from home_certificate import ensure as ensure_certificate, lan_address, local_name
from setup_sam3 import install as install_sam3

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:7878"
MODE = ROOT / "runtime/website-mode.txt"
PROJECT = "local-image-studio"
COMPOSE = ["compose", "--project-directory", str(ROOT), "-f", str(ROOT / "docker/compose.website.yaml"),
           "--env-file", str(ROOT / "runtime/studio-docker.env")]


def ready():
    try:
        with urlopen(URL + "/api/health", timeout=2) as response:
            payload = json.load(response)
        if payload.get("service") != "local-image-studio":
            raise SystemExit("Port 7878 is used by another application. Close it before starting the studio.")
        return True
    except (OSError, TimeoutError, ValueError):
        return False


def saved_mode():
    if MODE.exists():
        return MODE.read_text().strip()
    # Installs from before the website could run natively always used Docker.
    return "docker" if (ROOT / "runtime/studio-docker.env").exists() else "native"


def docker(*args, check=False):
    executable = shutil.which("docker") or "/usr/local/bin/docker"
    try:
        return subprocess.run([executable, *args], capture_output=True, text=True, check=check, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None


def docker_projects():
    """Compose projects started from this folder, including ones named by older versions."""
    result = docker("ps", "-a", "--filter", f"label=com.docker.compose.project.working_dir={ROOT}",
                    "--format", '{{.Label "com.docker.compose.project"}}')
    return set(result.stdout.split()) if result and result.returncode == 0 else set()


def remove_docker_websites(keep=None):
    for project in docker_projects() - {keep}:
        print(f"Removing the previous Docker website ({project}); your library stays in studio-data/.", flush=True)
        docker("compose", "-p", project, "down")


def start_docker(phone_url):
    if not services.MAC:
        raise SystemExit("On Linux, run the whole studio in Docker with `docker compose up -d` (docs/install-docker.md),\n"
                         "or serve the website natively: make start-native")
    check = docker("info", "--format", "{{.ServerVersion}}")
    if not check or check.returncode:
        raise SystemExit("Open Docker Desktop, wait for it to start, then try again.\n"
                         "Or run the website without Docker: make start-native")
    services.remove(services.WEBSITE)
    remove_docker_websites(keep=PROJECT)
    values = {"ENGINE_PROJECT_ROOT": str(ROOT), "STUDIO_UID": str(os.getuid()), "STUDIO_GID": str(os.getgid()),
              "STUDIO_LAN_URL": phone_url}
    (ROOT / "runtime/studio-docker.env").write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
    print("Starting the website in Docker…", flush=True)
    result = docker(*COMPOSE, "up", "-d", "--build")
    if not result or result.returncode:
        raise SystemExit("Docker could not start the website:\n" + (result.stderr if result else "docker is not installed"))


def start(mode, open_browser):
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    (ROOT / "studio-data").mkdir(exist_ok=True)
    address = lan_address()
    phone_url = f"http://{address}:7878"
    with (runtime / "web-launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        install_upscaler()
        try:
            install_sam3()
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"Automatic selection needs attention: {error}\nYou can still paint masks by hand.", flush=True)
        try:
            services.install_helper()
        except RuntimeError as error:
            raise SystemExit(str(error))
        try:
            ensure_certificate(address, local_name())
            app_url = f"https://{address}:7443"
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"The installable phone app needs attention: {error}\nThe website still works in the phone's browser.", flush=True)
            app_url = None
        if mode == "docker":
            start_docker(phone_url)
        else:
            remove_docker_websites()
            services.install_website(phone_url)
        MODE.write_text(mode + "\n")
        deadline = time.monotonic() + 60
        while not ready():
            if time.monotonic() > deadline:
                log = "docker compose logs" if mode == "docker" else "runtime/web-studio.log"
                raise SystemExit(f"The website didn’t start. Check {log}.")
            time.sleep(1)
    print(f"\nLocal Image Studio is ready\n  On this computer:          {URL}\n  On your phone (same Wi-Fi): {phone_url}", flush=True)
    if app_url:
        print(f"  The phone's first visit helps it trust this computer, then opens the installable app at {app_url}", flush=True)
    print(f"  Your PIN: {pin()}\n", flush=True)
    print("The studio keeps running in the background and starts again when you log in.\n"
          "The image engine starts when you make an image and sleeps after 10 idle minutes.", flush=True)
    if open_browser:
        webbrowser.open(URL)


def pin():
    path = ROOT / "studio-data/access-pin.txt"
    return path.read_text().strip() if path.exists() else "(created when the website first starts)"


def engine_state():
    try:
        token = (ROOT / "runtime/sam3-token.txt").read_text().strip()
        with urlopen(Request("http://127.0.0.1:7877/engine", headers={"Authorization": "Bearer " + token}), timeout=3) as response:
            return json.load(response).get("state", "unknown")
    except (OSError, ValueError):
        return "unavailable"


def status():
    mode = saved_mode()
    print("Local Image Studio")
    print(f"  Website: {'running' if ready() else 'stopped'} ({'Docker' if mode == 'docker' else 'native'}) · {URL}")
    print(f"  Helper:  {'running' if services.running(services.HELPER) else 'stopped'}")
    print(f"  Engine:  {engine_state()}")
    print(f"  Phone:   http://{lan_address()}:7878")
    print(f"  PIN:     {pin()}")


def stop():
    if services.engine_busy():
        raise SystemExit("The image engine is still working. Let current images finish, then stop the studio.")
    if saved_mode() == "docker":
        docker(*COMPOSE, "stop")
    services.stop(services.WEBSITE)
    services.stop(services.HELPER)
    print("Stopped. Your library and models are kept; the studio starts again at login or with: make start")


def uninstall():
    stop()
    remove_docker_websites()
    for name in (services.WEBSITE, services.HELPER):
        services.remove(name)
    print("Login services removed. Your library (studio-data/) and models (models/) were kept.\n"
          "To remove everything, delete this folder.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    begin = commands.add_parser("start", help="Start (or restart) the studio and print its address and PIN")
    where = begin.add_mutually_exclusive_group()
    where.add_argument("--docker", action="store_const", const="docker", dest="mode", help="Serve the website from Docker")
    where.add_argument("--native", action="store_const", const="native", dest="mode", help="Serve the website without Docker")
    begin.add_argument("--no-browser", action="store_true")
    commands.add_parser("status", help="Show what is running")
    commands.add_parser("stop", help="Stop the website, helper and engine until the next login")
    commands.add_parser("uninstall", help="Remove the login services; keep the library and models")
    commands.add_parser("pin", help="Print the website PIN")
    args = parser.parse_args()
    if args.command in (None, "start"):
        start(getattr(args, "mode", None) or saved_mode(), not getattr(args, "no_browser", False))
    else:
        {"status": status, "stop": stop, "uninstall": uninstall, "pin": lambda: print(pin())}[args.command]()


if __name__ == "__main__":
    main()

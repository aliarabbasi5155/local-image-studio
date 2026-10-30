#!/usr/bin/env python3
"""Install user systemd services for the independent Ubuntu Studio."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="Home-network IP address for HTTPS")
    args = parser.parse_args()
    from home_certificate import ensure, local_name
    ensure(args.address, local_name())
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    # systemd quotes are not shell quotes; escape its two special path characters.
    root = str(ROOT).replace("%", "%%").replace('"', '\\"')
    common = f'''[Service]
Type=simple
WorkingDirectory={root}
Environment="PATH={root}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="HF_HOME={root}/.cache/huggingface"
Environment="HF_HUB_OFFLINE=1"
Environment="ENGINE_CONTROL_URL=http://127.0.0.1:7877"
Environment="STUDIO_LAN_URL=https://{args.address}:7443"
Environment="SAM3_TIMEOUT=900"
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
'''
    for name, command in (("helper", "scripts/sam3_service.py"),):
        unit = f'''[Unit]
Description=Local Image Studio {name}
After=network.target

{common}ExecStart="{root}/.venv/bin/python" {command}

[Install]
WantedBy=default.target
'''
        (directory / f"local-image-studio-{name}.service").write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "local-image-studio-helper"], check=True)
    print("The GPU helper is ready. Start the website with scripts/start_linux_docker.sh")


if __name__ == "__main__":
    main()

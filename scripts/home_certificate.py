#!/usr/bin/env python3
"""Keep a home-network HTTPS certificate so phones can install the studio as an app."""
import argparse
import ipaddress
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENSSL = "/usr/bin/openssl"
# Name constraints limit the authority to home addresses, so its key cannot vouch for public websites.
NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
SERVER_DAYS = 800  # Apple accepts at most 825 days for TLS server certificates.
RENEW_SECONDS = 60 * 86400
CONFIG = """[req]
distinguished_name = name
[name]
[authority]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, permitted;DNS:local, permitted;DNS:localhost, {networks}
[server]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = {names}
"""


def openssl(*args, data=None):
    return subprocess.run([OPENSSL, *map(str, args)], input=data, capture_output=True, check=True).stdout


def local_name():
    if not shutil.which("scutil"):
        return socket.gethostname().split(".")[0].lower() + ".local"
    result = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True)
    return result.stdout.strip().lower() + ".local" if result.returncode == 0 and result.stdout.strip() else None


def subject_names(address, hostname):
    names = ["DNS:localhost", "IP:127.0.0.1"] + (["DNS:" + hostname] if hostname else [])
    try:
        if any(ipaddress.ip_address(address) in ipaddress.ip_network(network) for network in NETWORKS):
            names.append("IP:" + address)
    except ValueError:
        pass
    return ", ".join(dict.fromkeys(names))


def write(path, content, mode=0o644):
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as file:
        file.write(content)
    os.replace(temporary, path)


def ensure(address, hostname=None, runtime=ROOT / "runtime"):
    """Create the home authority once and reissue the server certificate when its addresses change.

    Phones trust runtime/tls/ca.crt. Only that folder reaches the website; the authority key stays
    in runtime/home-ca. Returns True when a new server certificate was written.
    """
    authority, tls = runtime / "home-ca", runtime / "tls"
    authority.mkdir(mode=0o700, parents=True, exist_ok=True)
    tls.mkdir(parents=True, exist_ok=True)
    names = subject_names(address, hostname)
    networks = ", ".join(f"permitted;IP:{n.network_address}/{n.netmask}" for n in map(ipaddress.ip_network, NETWORKS))
    with tempfile.TemporaryDirectory(dir=runtime) as work:
        config = Path(work) / "openssl.cnf"
        config.write_text(CONFIG.format(networks=networks, names=names))
        if not (authority / "ca.key").exists() or not (tls / "ca.crt").exists():
            write(authority / "ca.key", openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout"), 0o600)
            label = (hostname or "this Mac").removesuffix(".local")
            write(tls / "ca.crt", openssl("req", "-new", "-x509", "-key", authority / "ca.key", "-sha256", "-days", 3650,
                                          "-subj", f"/CN=Image Studio Home ({label})", "-config", config, "-extensions", "authority"))
            (tls / "server-names.txt").unlink(missing_ok=True)
        current = tls / "server-names.txt"
        fresh = subprocess.run([OPENSSL, "x509", "-noout", "-checkend", str(RENEW_SECONDS), "-in", str(tls / "server.crt")],
                               capture_output=True).returncode == 0
        if fresh and current.exists() and current.read_text() == names:
            return False
        key = openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout")
        write(Path(work) / "server.key", key, 0o600)
        request = openssl("req", "-new", "-key", Path(work) / "server.key", "-subj", "/CN=Image Studio", "-config", config)
        certificate = openssl("x509", "-req", "-CA", tls / "ca.crt", "-CAkey", authority / "ca.key", "-set_serial", secrets.randbits(63),
                              "-days", SERVER_DAYS, "-sha256", "-extfile", config, "-extensions", "server", data=request)
        # The website reloads when server.crt changes, so the matching key must already be in place.
        write(tls / "server.key", key, 0o600)
        write(tls / "server.crt", certificate)
        current.write_text(names)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="This Mac's Wi-Fi address, for example 192.168.1.20")
    print("Issued a new certificate." if ensure(parser.parse_args().address, local_name()) else "The certificate is current.")

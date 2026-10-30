import asyncio
import io
import json
import os
import re
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from scripts.home_certificate import OPENSSL, ensure
from studio.server import STATIC, create_app, tls_context

PHONE = {"Host": "192.168.1.20:7878"}


class PhoneAppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)  # Runs after the clients close.
        self.root = Path(self.temp.name)
        self.tls = self.root / "tls"
        self.tls.mkdir()
        (self.tls / "ca.crt").write_text(ssl.DER_cert_to_PEM_cert(b"home authority"))
        (self.tls / "server.crt").write_text("issued by the launcher")
        self.client = await self.start(create_app(self.root / "data", pin="123456", start_worker=False, tls_dir=self.tls))

    async def start(self, app, certificate=None, trust=None):
        server = TestServer(app)
        await server.start_server(ssl=certificate)  # TestServer reads ssl only here.
        client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True), connector=aiohttp.TCPConnector(ssl=trust) if trust else None)
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def test_installable_app_files_are_served_and_cached_shell_matches_allowlist(self):
        worker = await self.client.get("/sw.js")
        self.assertEqual(worker.status, 200)
        self.assertEqual(worker.headers["Cache-Control"], "no-cache")
        self.assertIn("javascript", worker.content_type)
        shell = re.findall(r"'(/[^']*)'", re.search(r"const SHELL = \[(.*?)\];", await worker.text(), re.S).group(1))
        index = (STATIC / "index.html").read_text()
        for path in shell:
            self.assertEqual((await self.client.get(path)).status, 200, path)
        for asset in re.findall(r'(?:href|src)="(/static/[^"]+\.(?:css|js))"', index):
            self.assertIn(asset, shell)
        manifest = json.loads(await (await self.client.get("/static/manifest.json")).text())
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["display"]), ("/", "/", "standalone"))
        purposes = set()
        for icon in manifest["icons"]:
            response = await self.client.get(icon["src"])
            self.assertEqual(response.status, 200, icon["src"])
            purposes.add(icon["purpose"])
            if icon["type"] == "image/png":
                image = Image.open(io.BytesIO(await response.read()))
                self.assertEqual("%dx%d" % image.size, icon["sizes"])
                if icon["purpose"] == "maskable":
                    self.assertEqual(image.mode, "RGB")
        self.assertEqual(purposes, {"any", "maskable"})
        self.assertIn('rel="apple-touch-icon" href="/static/apple-touch-icon.png"', index)
        touch = Image.open(io.BytesIO(await (await self.client.get("/static/apple-touch-icon.png")).read()))
        self.assertEqual((touch.size, touch.mode), ((180, 180), "RGB"))

    async def test_phones_on_plain_http_are_sent_to_setup_while_this_mac_keeps_http(self):
        response = await self.client.get("/", headers=PHONE, allow_redirects=False)
        self.assertEqual((response.status, response.headers["Location"], response.headers["Cache-Control"]), (302, "/setup", "no-store"))
        # The PIN and library never cross the Wi-Fi unencrypted once HTTPS is ready.
        self.assertEqual((await self.client.post("/api/login", json={"pin": "123456"}, headers=PHONE)).status, 403)
        self.assertEqual((await self.client.get("/api/options", headers=PHONE)).status, 403)
        self.assertEqual((await self.client.get("/media/image/anything", headers=PHONE)).status, 403)
        self.assertEqual((await self.client.get("/api/health", headers=PHONE)).status, 200)
        setup = await self.client.get("/setup", headers=PHONE)
        self.assertEqual(setup.status, 200)
        self.assertIn("connect-src 'self' https:", setup.headers["Content-Security-Policy"])
        self.assertIn("/setup/image-studio-home.crt", await setup.text())
        for asset in ("/static/setup.js", "/static/setup.css", "/static/style.css"):
            self.assertEqual((await self.client.get(asset, headers=PHONE)).status, 200, asset)
        certificate = await self.client.get("/setup/image-studio-home.crt", headers=PHONE)
        self.assertEqual((certificate.status, certificate.content_type), (200, "application/x-x509-ca-cert"))
        self.assertEqual(await certificate.read(), b"home authority")
        details = await (await self.client.get("/setup/certificate.json", headers=PHONE)).json()
        self.assertIn(details["filename"], certificate.headers["Content-Disposition"])
        self.assertRegex(details["filename"], r"image-studio-home-[0-9a-f]{12}\.crt")
        self.assertEqual(len(details["fingerprint_sha256"].split(":")),32)
        self.assertEqual((await self.client.get("/")).status, 200)
        login = await self.client.post("/api/login", json={"pin": "123456"})
        self.assertEqual(login.status, 200)
        self.assertFalse(login.cookies["studio_session"]["secure"])

    async def test_without_https_phones_keep_using_plain_http(self):
        client = await self.start(create_app(self.root / "plain", pin="123456", start_worker=False, tls_dir=self.root / "none"))
        self.assertEqual((await client.get("/", headers=PHONE, allow_redirects=False)).status, 200)
        self.assertEqual((await client.post("/api/login", json={"pin": "123456"}, headers=PHONE)).status, 200)
        self.assertEqual((await client.get("/setup/image-studio-home.crt")).status, 404)

    @unittest.skipUnless(os.path.exists(OPENSSL), "needs the system openssl")
    async def test_https_uses_issued_certificate_renews_live_and_rejects_public_names(self):
        runtime = self.root / "runtime"
        self.assertTrue(ensure("192.168.1.20", "studio-test.local", runtime))
        self.assertFalse(ensure("192.168.1.20", "studio-test.local", runtime))
        self.assertEqual(oct((runtime / "home-ca/ca.key").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct((runtime / "tls/server.key").stat().st_mode & 0o777), "0o600")
        self.assertFalse((runtime / "tls/ca.key").exists())
        tls = runtime / "tls"
        trust = ssl.create_default_context(cafile=str(tls / "ca.crt"))
        client = await self.start(create_app(self.root / "secure", pin="123456", start_worker=False, tls_dir=tls), tls_context(tls), trust)
        self.assertEqual(client.make_url("/").scheme, "https")
        self.assertEqual((await client.get("/", headers={"Host": "192.168.1.20:7443"})).status, 200)
        login = await client.post("/api/login", json={"pin": "123456"}, headers={"Host": "192.168.1.20:7443"})
        self.assertEqual(login.status, 200)
        self.assertTrue(login.cookies["studio_session"]["secure"])
        self.assertEqual((await client.get("/api/options")).status, 200)

        async def served_addresses():
            reader, writer = await asyncio.open_connection("127.0.0.1", client.port, ssl=trust)
            names = {value for _, value in writer.get_extra_info("peercert")["subjectAltName"]}
            writer.close()
            return names
        self.assertIn("192.168.1.20", await served_addresses())
        details = await (await client.get("/setup/certificate.json")).json()
        self.assertEqual(details["name"], "Image Studio Home (studio-test)")
        self.assertIn("studio-test", details["filename"])
        self.assertTrue(ensure("192.168.1.21", "studio-test.local", runtime))
        self.assertEqual(await served_addresses(), {"localhost", "studio-test.local", "127.0.0.1", "192.168.1.21"})

        # Even with the CA key, a certificate for a public website is rejected by the name constraints.
        config = self.root / "public.cnf"
        config.write_text("[req]\ndistinguished_name=name\n[name]\n[public]\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:example.com\n")
        subprocess.run([OPENSSL, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(self.root / "public.key")], check=True)
        request = subprocess.run([OPENSSL, "req", "-new", "-key", str(self.root / "public.key"), "-subj", "/CN=example.com", "-config", str(config)],
                                 capture_output=True, check=True).stdout
        subprocess.run([OPENSSL, "x509", "-req", "-CA", str(tls / "ca.crt"), "-CAkey", str(runtime / "home-ca/ca.key"), "-set_serial", "7", "-sha256",
                        "-days", "30", "-extfile", str(config), "-extensions", "public", "-out", str(self.root / "public.crt")],
                       input=request, capture_output=True, check=True)
        public = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        public.load_cert_chain(self.root / "public.crt", self.root / "public.key")
        server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0, ssl=public)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        with self.assertRaisesRegex(ssl.SSLCertVerificationError, "permitted subtree"):
            await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1], ssl=trust, server_hostname="example.com")

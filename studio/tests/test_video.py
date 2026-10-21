import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from studio.tests import test_studio
from studio.tests.test_studio import photo
from studio.video import validate_recipe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from video_native import NativeVideo


class VideoTests(unittest.IsolatedAsyncioTestCase):
    login = test_studio.APITests.login
    asyncTearDown = test_studio.APITests.asyncTearDown

    async def asyncSetUp(self):
        await test_studio.APITests.asyncSetUp(self)
        self.videos = self.app[self.state_key()]["videos"]
        self.videos.native = AsyncMock(return_value={"ready": True})

    @staticmethod
    def state_key():
        from studio.server import STATE
        return STATE

    async def source(self):
        await self.login()
        source_id = "test-source"
        (self.store.directory / "uploads" / (source_id + ".png")).write_bytes(photo())
        self.store.execute("INSERT INTO uploads VALUES (?,?,?,?,?,?)", (source_id, time.time(), "Photo", 128, 128, "digest"))
        return source_id

    async def submit_video(self, **changes):
        return await self.client.post("/api/videos", json={"source_id": "test-source", "prompt": "The leaves sway gently.", **changes})

    async def test_video_auth_and_validation(self):
        self.assertEqual((await self.client.get("/api/videos")).status, 401)
        self.assertEqual((await self.client.get("/media/video/anything")).status, 401)
        await self.source()
        for change in ({"prompt": ""}, {"frames": 48}, {"frames": True}, {"seed": True}, {"seed": -1},
                       {"shape": "4k"}, {"source_id": "../../etc/passwd"}, {"shape": []}):
            self.assertEqual((await self.submit_video(**change)).status, 400, change)
        self.videos.native.return_value = {"ready": False, "error": "Setup required"}
        self.assertEqual((await self.submit_video()).status, 503)

    async def test_video_queue_limit_cancel_and_source_copy(self):
        await self.source()
        ids = []
        for _ in range(4):
            response = await self.submit_video(seed=10)
            self.assertEqual(response.status, 202)
            ids.append((await response.json())["id"])
        self.assertEqual((await self.submit_video()).status, 429)
        original = self.store.directory / "uploads/test-source.png"
        copied = self.videos.directory / (ids[0] + ".png")
        self.assertEqual(copied.read_bytes(), original.read_bytes())
        original.unlink()
        self.assertTrue(copied.is_file())
        await self.client.post(f"/api/videos/{ids[0]}/cancel", json={})
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (ids[0],))["status"], "cancelled")
        recipe = await (await self.client.get(f"/api/videos/{ids[0]}/recipe")).json()
        self.assertEqual(recipe["seed"], 10)

    async def test_video_download_and_range_are_protected(self):
        await self.source()
        video_id = (await (await self.submit_video()).json())["id"]
        self.assertEqual((await self.client.get(f"/media/video/{video_id}")).status, 404)
        payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 100
        (self.videos.directory / (video_id + ".mp4")).write_bytes(payload)
        self.store.execute("UPDATE videos SET status='complete' WHERE id=?", (video_id,))
        response = await self.client.get(f"/media/video/{video_id}?download=1", headers={"Range": "bytes=0-11"})
        self.assertEqual(response.status, 206)
        self.assertEqual(await response.read(), payload[:12])
        self.assertEqual(response.content_type, "video/mp4")
        self.assertIn("attachment", response.headers["Content-Disposition"])

    async def test_video_restart_marks_running_failed_and_keeps_queue(self):
        from studio.video import Videos
        await self.source()
        first = (await (await self.submit_video()).json())["id"]
        second = (await (await self.submit_video()).json())["id"]
        self.store.execute("UPDATE videos SET status='running' WHERE id=?", (first,))
        Videos(self.store, self.videos.engine)
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (first,))["status"], "failed")
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (second,))["status"], "queued")

    async def test_concurrent_admission_respects_limit_and_bounds_large_photo(self):
        await self.source()
        source = self.store.directory / "uploads/test-source.png"
        Image.new("RGB", (4096, 2048), "navy").save(source)
        responses = await asyncio.gather(*(self.submit_video() for _ in range(6)))
        self.assertEqual(sorted(response.status for response in responses), [202, 202, 202, 202, 429, 429])
        video_id = (await next(response for response in responses if response.status == 202).json())["id"]
        with Image.open(self.videos.directory / (video_id + ".png")) as image:
            self.assertEqual(image.size, (1536, 768))
        with Image.open(source) as image:
            self.assertEqual(image.size, (4096, 2048))

    async def test_worker_serializes_gpu_and_saves_mp4(self):
        await self.source()
        video_id = (await (await self.submit_video()).json())["id"]
        payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 100
        async def generate(request):
            self.assertTrue(self.videos.engine.resource_lock.locked())
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            reader = await request.multipart()
            recipe = json.loads(await (await reader.next()).text())
            self.assertEqual(recipe["id"], video_id)
            self.assertEqual(await (await reader.next()).read(), photo())
            return web.Response(body=payload, content_type="video/mp4")
        helper = web.Application()
        helper.router.add_post("/video/generate", generate)
        async with TestServer(helper) as server:
            token = self.store.directory / "helper-token"
            token.write_text("test-token")
            self.videos.engine.native.base = str(server.make_url("")).rstrip("/")
            self.videos.engine.native.token_file = token
            row = self.store.one("SELECT * FROM videos WHERE id=?", (video_id,))
            await self.videos.engine.resource_lock.acquire()
            task = asyncio.create_task(self.videos.generate(row))
            await asyncio.sleep(.02)
            self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"], "queued")
            self.videos.engine.resource_lock.release()
            await task
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"], "complete")
        self.assertEqual((self.videos.directory / (video_id + ".mp4")).read_bytes(), payload)
        self.assertFalse((self.videos.directory / (video_id + ".part")).exists())

    async def test_cancel_running_video_releases_lock_and_continues_worker(self):
        await self.source()
        video_id = (await (await self.submit_video()).json())["id"]
        started, stopped = asyncio.Event(), asyncio.Event()
        async def generate(row):
            async with self.videos.engine.resource_lock:
                self.store.execute("UPDATE videos SET status='running' WHERE id=?", (row["id"],))
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
        self.videos.generate = generate
        worker = asyncio.create_task(self.videos.worker())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await self.client.post(f"/api/videos/{video_id}/cancel", json={})
            await asyncio.wait_for(stopped.wait(), 2)
            self.assertFalse(self.videos.engine.resource_lock.locked())
            self.assertFalse(worker.done())
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def test_native_command_uses_pinned_local_models_and_literal_prompt(self):
        recipe = validate_recipe({"prompt": "Leaves sway; $(touch /tmp/not-executed)", "seed": 0})
        native = NativeVideo(Path("/project"), None, None, None)
        command = native.command(recipe, Path("/photo with spaces.png"), Path("/out.mp4"))
        self.assertEqual(command[command.index("--prompt") + 1], recipe["prompt"])
        self.assertIn("--low-ram", command)
        self.assertIn("--distilled", command)
        self.assertEqual(command[command.index("--frames") + 1], "49")


class NativeVideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        self.engine = Mock(lock=asyncio.Lock(), process=None)
        self.engine.state = AsyncMock(return_value="sleeping")
        self.engine.stop = AsyncMock()
        self.busy = AsyncMock(return_value=False)
        self.native = NativeVideo(self.root, self.engine, asyncio.Lock(), self.busy)
        self.native.ready = lambda: True
        self.native.command = lambda recipe, source, output: [sys.executable, "-c",
            "import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(bytes.fromhex('00000018667479706d703432'))", str(output)]
        app = web.Application()
        app.router.add_post("/video/generate", self.native.generate)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def form(self):
        data = aiohttp.FormData()
        data.add_field("recipe", json.dumps({"prompt": "A fox moves its head", "seed": 42}))
        data.add_field("image", photo(), filename="photo.png", content_type="image/png")
        return data

    async def test_native_stream_is_read_before_temp_cleanup(self):
        response = await self.client.post("/video/generate", data=self.form())
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.read())[4:8], b"ftyp")
        self.engine.stop.assert_awaited_once()
        self.assertFalse(self.native.busy)
        self.assertFalse(self.native.lock.locked())
        self.assertFalse(list((self.root / "runtime").glob("studio-video-*")))

    async def test_native_refuses_busy_and_external_image_engine(self):
        self.busy.return_value = True
        self.assertEqual((await self.client.post("/video/generate", data=self.form())).status, 409)
        self.busy.return_value = False
        self.engine.state.return_value = "ready"
        self.assertEqual((await self.client.post("/video/generate", data=self.form())).status, 409)
        self.engine.stop.assert_not_awaited()

    async def test_disconnect_terminates_native_inference_and_releases_locks(self):
        marker = self.root / "started"
        self.native.command = lambda *args: [sys.executable, "-c",
            "import time,sys; from pathlib import Path; Path(sys.argv[1]).write_text('started'); time.sleep(120)", str(marker)]
        request = asyncio.create_task(self.client.post("/video/generate", data=self.form()))
        try:
            async with asyncio.timeout(3):
                while not marker.exists():
                    await asyncio.sleep(.01)
            self.assertTrue(self.native.busy)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            async with asyncio.timeout(3):
                while self.native.busy:
                    await asyncio.sleep(.01)
            self.assertFalse(self.native.lock.locked())
            self.assertFalse(self.engine.lock.locked())
            self.assertFalse(list((self.root / "runtime").glob("studio-video-*")))
        finally:
            request.cancel()

import asyncio
import json
import io
import shutil
import subprocess
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
from studio.video import validate_recipe, plan_video_size, save_video_source

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from video_native import NativeVideo


class VideoTests(unittest.IsolatedAsyncioTestCase):
    login = test_studio.APITests.login
    asyncTearDown = test_studio.APITests.asyncTearDown

    async def asyncSetUp(self):
        await test_studio.APITests.asyncSetUp(self)
        self.videos = self.app[self.state_key()]["videos"]
        self.videos.native = AsyncMock(return_value={"ready": True, "sizing_version": 2})

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

    async def test_clips_live_in_their_project_and_go_with_it(self):
        await self.login()
        form = aiohttp.FormData()
        form.add_field("image", photo(), filename="harbor.png", content_type="image/png")
        upload = await (await self.client.post("/api/uploads", data=form)).json()
        queued = (await (await self.submit_video(source_id=upload["id"])).json())["id"]
        project = await (await self.client.get(f"/api/projects/{upload['project_id']}")).json()
        clip, = project["videos"]
        self.assertEqual((clip["id"], clip["kind"], clip["status"], clip["ready"]), (queued, "video", "queued", False))
        self.assertEqual((clip["parent"]["id"], clip["thumbnail"]), (upload["id"], f"/media/thumb/{upload['id']}"))
        self.assertEqual((await self.client.get(clip["thumbnail"])).status, 200)
        card, = (await (await self.client.get("/api/projects")).json())["projects"]
        self.assertEqual(([v["id"] for v in card["animating"]], card["cover"]["id"], card["updated"]), ([queued], upload["id"], clip["created"]))
        # A queued clip blocks deleting it or its project, as a queued image does.
        self.assertEqual((await self.client.delete(f"/api/projects/{upload['project_id']}", json={"confirm": True})).status, 409)
        self.assertEqual((await self.client.delete(f"/api/videos/{queued}", json={"confirm": True})).status, 409)
        await self.client.post(f"/api/videos/{queued}/cancel", json={})
        self.assertEqual((await self.client.delete(f"/api/videos/{queued}", json={})).status, 400)
        self.assertEqual((await self.client.delete(f"/api/videos/{queued}", json={"confirm": True})).status, 200)
        self.assertFalse((self.videos.directory / (queued + ".png")).exists())
        finished = (await (await self.submit_video(source_id=upload["id"])).json())["id"]
        self.store.execute("UPDATE videos SET status='complete' WHERE id=?", (finished,))
        (self.videos.directory / (finished + ".mp4")).write_bytes(b"mp4")
        self.assertEqual((await self.client.get(f"/api/projects/{upload['project_id']}")).status, 200)
        self.assertEqual((await self.client.delete(f"/api/projects/{upload['project_id']}", json={"confirm": True})).status, 200)
        self.assertIsNone(self.store.one("SELECT id FROM videos WHERE id=?", (finished,)))
        self.assertFalse((self.videos.directory / (finished + ".mp4")).exists())

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
        self.store.execute("UPDATE uploads SET width=4096,height=2048 WHERE id='test-source'")
        responses = await asyncio.gather(*(self.submit_video() for _ in range(6)))
        self.assertEqual(sorted(response.status for response in responses), [202, 202, 202, 202, 429, 429])
        video_id = (await next(response for response in responses if response.status == 202).json())["id"]
        with Image.open(self.videos.directory / (video_id + ".png")) as image:
            recipe = json.loads(self.store.one("SELECT recipe FROM videos WHERE id=?", (video_id,))["recipe"])
            self.assertEqual(image.size, (recipe["generation_width"], recipe["generation_height"]))
            self.assertLessEqual(image.width, 768)
            self.assertEqual((recipe["width"], recipe["height"]), (4096, 2048))
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

    async def test_six_seconds_and_size_come_from_saved_photo(self):
        await self.source()
        Image.new("RGB", (1001, 667), "navy").save(self.store.directory / "uploads/test-source.png")
        self.store.execute("UPDATE uploads SET width=1001,height=667 WHERE id='test-source'")
        response = await self.submit_video(frames=145, output_size="original", width=2048, source_width=2000)
        self.assertEqual(response.status, 202)
        video_id = (await response.json())["id"]
        recipe = json.loads(self.store.one("SELECT recipe FROM videos WHERE id=?", (video_id,))["recipe"])
        self.assertEqual((recipe["width"], recipe["height"], recipe["frames"]), (1001, 667, 145))
        command = NativeVideo(Path("/project"), None, None, None).command(recipe, Path("input.png"), Path("out.mp4"))
        self.assertEqual(command[command.index("--frames") + 1], "145")
        self.assertEqual(int(command[command.index("--width") + 1]), recipe["generation_width"])
        self.assertNotEqual(recipe["width"], recipe["generation_width"])
        self.videos.native.return_value = {"ready": True}
        self.assertEqual((await self.submit_video()).status, 503)


class VideoSizeTests(unittest.TestCase):
    def test_output_ratio_is_exact_for_common_and_coprime_sizes(self):
        for width, height in ((4032, 3024), (3024, 4032), (1001, 667), (1920, 1080), (1024, 1024), (8192, 16)):
            for size in ("original", "preview"):
                with self.subTest(dimensions=(width, height), mode=size):
                    plan = plan_video_size(width, height, size)
                    self.assertEqual(plan["width"] * height, plan["height"] * width)
                    self.assertEqual(plan["generation_width"] % 64, 0)
                    self.assertEqual(plan["generation_height"] % 64, 0)
                    self.assertLessEqual(max(plan["generation_width"], plan["generation_height"]), 768)
                    if size == "original":
                        self.assertEqual((plan["width"], plan["height"]), (width, height))
        self.assertEqual((plan_video_size(1920, 1080, "preview")["width"], plan_video_size(1920, 1080, "preview")["height"]), (672, 378))

    def test_preparation_keeps_all_four_edges_and_only_adds_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.png", Path(folder) / "padded.png"
            image = Image.new("RGB", (161, 97), "navy")
            for point, color in (((0, 0), "red"), ((160, 0), "green"), ((0, 96), "blue"), ((160, 96), "white")):
                image.putpixel(point, Image.new("RGB", (1, 1), color).getpixel((0, 0)))
            image.save(source)
            plan = plan_video_size(161, 97)
            save_video_source(source, target, plan)
            with Image.open(target) as prepared:
                left, top = plan["pad_left"], plan["pad_top"]
                self.assertEqual(prepared.crop((left, top, left + 161, top + 97)).tobytes(), image.tobytes())
                self.assertEqual(prepared.getpixel((0, 0)), image.getpixel((0, 0)))

    def test_invalid_sizes_and_lengths_are_rejected(self):
        for size in ((True, 512), (0, 512), (9000, 512), (8192, 8192)):
            with self.assertRaises(ValueError):
                plan_video_size(*size)
        for frames in (144, 146, 0, True):
            with self.assertRaises(ValueError):
                validate_recipe({"prompt": "Motion", "frames": frames}, (1024, 768))


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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is needed for real encoding checks")
    async def test_real_mp4_finishing_keeps_six_seconds_audio_and_exact_odd_or_even_size(self):
        self.native.command = lambda recipe, source, output: ["ffmpeg", "-v", "error", "-y",
            "-loop", "1", "-framerate", "24", "-i", str(source), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-frames:v", str(recipe["frames"]), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output)]
        for dimensions in ((161, 97), (800, 600)):
            with self.subTest(dimensions=dimensions):
                source, padded = self.root / "photo.png", self.root / "prepared.png"
                Image.new("RGB", dimensions, (70, 130, 190)).save(source)
                recipe = validate_recipe({"prompt": "Gentle movement", "frames": 145, "seed": 42}, dimensions)
                save_video_source(source, padded, recipe)
                form = aiohttp.FormData()
                form.add_field("recipe", json.dumps(recipe))
                form.add_field("image", padded.read_bytes(), filename="photo.png", content_type="image/png")
                response = await self.client.post("/video/generate", data=form)
                self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
                output = self.root / "download.mp4"
                output.write_bytes(await response.read())
                probe = json.loads(await asyncio.to_thread(subprocess.check_output,
                    ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)], text=True))
                video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
                self.assertEqual((video["width"], video["height"]), dimensions)
                self.assertEqual(video["nb_frames"], "145")
                self.assertAlmostEqual(float(video["duration"]), 145 / 24, places=3)
                self.assertEqual(video["sample_aspect_ratio"], "1:1")
                self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))
                frame = await asyncio.to_thread(subprocess.check_output, ["ffmpeg", "-v", "error", "-i", str(output),
                    "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"])
                with Image.open(io.BytesIO(frame)) as decoded:
                    for point in ((0, 0), (dimensions[0]-1, dimensions[1]-1)):
                        self.assertTrue(all(abs(a-b) < 10 for a, b in zip(decoded.convert("RGB").getpixel(point), (70, 130, 190))))

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

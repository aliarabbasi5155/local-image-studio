"""Durable image-to-video jobs, serialized with image generation on the Mac."""
import asyncio
import contextlib
import json
import secrets
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, ImageOps

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/video.json").read_text())
SIZES = {"landscape": (768, 512), "portrait": (512, 768), "square": (512, 512)}


def save_video_source(source, target):
    # Bound transfer and image-conditioning memory even for a large generated PNG.
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
        image.save(target, "PNG")


def validate_recipe(payload):
    if not isinstance(payload, dict):
        raise ValueError("Choose a photo and describe its motion.")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError("Describe the motion in 1–2000 characters.")
    shape = payload.get("shape", "landscape")
    frames = payload.get("frames", 49)
    seed = payload.get("seed")
    if seed is None:
        seed = secrets.randbelow(2**32)
    if not isinstance(shape, str) or shape not in SIZES:
        raise ValueError("Choose landscape, portrait or square.")
    if type(frames) is not int or frames not in (49, 97):
        raise ValueError("Choose a 2 or 4 second clip.")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Seed must be between 0 and 4294967295.")
    width, height = SIZES[shape]
    return {"prompt": prompt.strip(), "shape": shape, "width": width, "height": height,
            "frames": frames, "fps": 24, "seed": seed, "model": CONFIG["label"],
            "model_revision": CONFIG["model"]["revision"], "runtime_revision": CONFIG["runtime_revision"]}


class Videos:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.directory = store.directory / "videos"
        self.directory.mkdir(exist_ok=True)
        store.execute("""CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
            recipe TEXT NOT NULL, error TEXT, finished REAL)""")
        # A disconnected native request is cancelled, never silently re-submitted.
        store.execute("UPDATE videos SET status='failed', error='Studio restarted during video generation. Submit again to retry.' WHERE status='running'")
        self.wake = asyncio.Event()
        self.submission_lock = asyncio.Lock()
        self.current = None
        self.task = None

    async def native(self, method, path, **kwargs):
        native = self.engine.native
        if not native.base:
            raise RuntimeError("Start Studio using its launcher to connect the native video helper.")
        headers = {"Authorization": "Bearer " + native.token_file.read_text().strip()}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.request(method, native.base + path, headers=headers, **kwargs) as response:
                if response.status == 404:
                    raise RuntimeError("Restart the Mac helper to enable video generation.")
                result = await response.json()
                if response.status != 200:
                    raise RuntimeError(result.get("error", "Video helper unavailable."))
                return result

    async def options(self, request):
        try:
            status = await self.native("GET", "/video/status")
        except (OSError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            status = {"ready": False, "error": str(error)}
        return web.json_response({**status, "model": CONFIG["label"]})

    async def submit(self, request):
        payload = await request.json()
        recipe = validate_recipe(payload)
        source_id = payload.get("source_id")
        source = self.store.one("SELECT id FROM uploads WHERE id=?", (source_id,)) if isinstance(source_id, str) else None
        if not source:
            raise ValueError("Choose an uploaded or saved image first.")
        try:
            status = await self.native("GET", "/video/status")
        except (OSError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            return web.json_response({"error": str(error)}, status=503)
        if not status.get("ready"):
            return web.json_response({"error": status.get("error", "Install the video model first.")}, status=503)
        async with self.submission_lock:
            if self.store.one("SELECT COUNT(*) AS n FROM videos WHERE status IN ('queued','running')")["n"] >= 4:
                return web.json_response({"error": "Finish or cancel a video before adding more."}, status=429)
            video_id = str(uuid.uuid4())
            recipe.update(source_id=source_id, id=video_id, source_long_edge_limit=1536)
            # Independent source survives later image/project deletion.
            source_path = self.store.directory / "uploads" / (source_id + ".png")
            try:
                save_video_source(source_path, self.directory / (video_id + ".png"))
                self.store.execute("INSERT INTO videos (id,created,status,recipe) VALUES (?,?,?,?)",
                                   (video_id, time.time(), "queued", json.dumps(recipe)))
            except BaseException:
                (self.directory / (video_id + ".png")).unlink(missing_ok=True)
                raise
        self.wake.set()
        return web.json_response({"id": video_id}, status=202)

    async def library(self, request):
        rows = self.store.rows("SELECT * FROM videos ORDER BY created DESC LIMIT 100")
        for row in rows:
            row["recipe"] = json.loads(row["recipe"])
            row["url"] = f'/media/video/{row["id"]}' if row["status"] == "complete" else None
        return web.json_response({"videos": rows})

    async def cancel(self, request):
        video_id = request.match_info["id"]
        row = self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))
        if not row:
            raise web.HTTPNotFound()
        if row["status"] in ("queued", "running"):
            self.store.execute("UPDATE videos SET status='cancelled',finished=? WHERE id=?", (time.time(), video_id))
            if self.current == video_id and self.task:
                self.task.cancel()
        return web.json_response({"ok": True})

    async def media(self, request):
        video_id = request.match_info["id"]
        row = self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))
        if not row or row["status"] != "complete":
            raise web.HTTPNotFound()
        headers = {"Cache-Control": "no-store", "Content-Type": "video/mp4"}
        if request.query.get("download") == "1":
            headers["Content-Disposition"] = f'attachment; filename="{video_id}.mp4"'
        return web.FileResponse(self.directory / (video_id + ".mp4"), headers=headers)

    async def recipe(self, request):
        row = self.store.one("SELECT recipe FROM videos WHERE id=?", (request.match_info["id"],))
        if not row:
            raise web.HTTPNotFound()
        return web.json_response(json.loads(row["recipe"]), headers={"Content-Disposition": 'attachment; filename="video-recipe.json"'})

    async def generate(self, row):
        video_id = row["id"]
        temporary = self.directory / (video_id + ".part")
        try:
            async with self.engine.resource_lock:
                if self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"] != "queued":
                    return
                self.store.execute("UPDATE videos SET status='running' WHERE id=?", (video_id,))
                native = self.engine.native
                headers = {"Authorization": "Bearer " + native.token_file.read_text().strip()}
                with (self.directory / (video_id + ".png")).open("rb") as source:
                    form = aiohttp.FormData()
                    form.add_field("recipe", row["recipe"])
                    form.add_field("image", source, filename="source.png", content_type="image/png")
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7200)) as session:
                        async with session.post(native.base + "/video/generate", headers=headers, data=form) as response:
                            if response.status != 200:
                                result = await response.json()
                                raise RuntimeError(result.get("error", "Video generation failed."))
                            if response.content_type != "video/mp4":
                                raise RuntimeError("The video helper returned an invalid result.")
                            with temporary.open("wb") as output:
                                async for chunk in response.content.iter_chunked(1024 * 1024):
                                    output.write(chunk)
                with temporary.open("rb") as output:
                    if output.read(12)[4:8] != b"ftyp":
                        raise RuntimeError("The video helper did not produce an MP4 file.")
                temporary.replace(self.directory / (video_id + ".mp4"))
                self.store.execute("UPDATE videos SET status='complete',finished=? WHERE id=?", (time.time(), video_id))
        finally:
            temporary.unlink(missing_ok=True)

    async def worker(self):
        while True:
            row = self.store.one("SELECT * FROM videos WHERE status='queued' ORDER BY created LIMIT 1")
            if not row:
                self.wake.clear()
                await self.wake.wait()
                continue
            self.current = row["id"]
            self.task = asyncio.create_task(self.generate(row))
            try:
                await self.task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except (OSError, RuntimeError, ValueError, aiohttp.ClientError, asyncio.TimeoutError) as error:
                self.store.execute("UPDATE videos SET status='failed',error=?,finished=? WHERE id=? AND status!='cancelled'",
                                   (str(error)[:1500], time.time(), row["id"]))
            finally:
                self.current, self.task = None, None

    async def context(self, app):
        task = asyncio.create_task(self.worker())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

"""Durable image-to-video jobs, serialized with image generation on the Mac."""
import asyncio
import contextlib
import json
import math
import secrets
import time
import uuid
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import web
from PIL import Image, ImageOps

from .media import media_file

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/video.json").read_text())
SIZES = {"landscape": (768, 512), "portrait": (512, 768), "square": (512, 512)}


def save_video_source(source, target, recipe=None):
    # Bound transfer and image-conditioning memory even for a large generated PNG.
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        if recipe and recipe.get("sizing_version") == 2:
            image = image.resize((recipe["content_width"], recipe["content_height"]), Image.Resampling.LANCZOS)
            left, top = recipe["pad_left"], recipe["pad_top"]
            right = recipe["generation_width"] - image.width - left
            bottom = recipe["generation_height"] - image.height - top
            image = Image.fromarray(np.pad(np.asarray(image), ((top, bottom), (left, right), (0, 0)), mode="edge"))
        else:
            image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
        image.save(target, "PNG")


def plan_video_size(width, height, output_size="original"):
    if (type(width) is not int or type(height) is not int or min(width, height) < 16
            or max(width, height) > 8192 or width * height > 16_777_216):
        raise ValueError("Video photos must be 16–8192 pixels per edge and at most 16 megapixels.")
    if output_size not in ("preview", "original"):
        raise ValueError("Choose Preview or Match photo.")
    # Integer multiples of the reduced ratio preserve it exactly, even for odd sizes.
    divisor = math.gcd(width, height)
    unit_w, unit_h = width // divisor, height // divisor
    factor = max(1, min(divisor, math.floor(min(768 / unit_w, 768 / unit_h,
                                               math.sqrt(262144 / (unit_w * unit_h))))))
    output_w, output_h = (width, height) if output_size == "original" else (unit_w * factor, unit_h * factor)
    scale = min(1, 768 / output_w, 768 / output_h, math.sqrt(262144 / (output_w * output_h)))
    content_w, content_h = max(1, round(output_w * scale)), max(1, round(output_h * scale))
    canvas_w, canvas_h = math.ceil(content_w / 64) * 64, math.ceil(content_h / 64) * 64
    return {"sizing_version": 2, "output_size": output_size, "source_width": width, "source_height": height,
            "width": output_w, "height": output_h, "generation_width": canvas_w, "generation_height": canvas_h,
            "content_width": content_w, "content_height": content_h,
            "pad_left": (canvas_w - content_w) // 2, "pad_top": (canvas_h - content_h) // 2}


def validate_recipe(payload, source_size=None):
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
    if type(frames) is not int or frames not in (49, 97, 145):
        raise ValueError("Choose a 2, 4 or 6 second clip.")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Seed must be between 0 and 4294967295.")
    width, height = SIZES[shape]
    result = {"prompt": prompt.strip(), "shape": shape, "width": width, "height": height,
            "frames": frames, "fps": 24, "seed": seed, "model": CONFIG["label"],
            "model_revision": CONFIG["model"]["revision"], "runtime_revision": CONFIG["runtime_revision"]}
    if source_size is not None or payload.get("sizing_version") == 2:
        if "shape" in payload:
            raise ValueError("Video shape follows the photo automatically. Reload Studio to update its controls.")
        source_size = source_size or (payload.get("source_width"), payload.get("source_height"))
        result.pop("shape")
        result.update(plan_video_size(*source_size, payload.get("output_size", "original")))
    return result


class Videos:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.directory = store.directory / "videos"
        self.directory.mkdir(exist_ok=True)
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
        if status.get("ready") and status.get("sizing_version", 0) < 2:
            status.update(ready=False, error="Restart the Mac helper to enable photo-sized videos.")
        return web.json_response({**status, "model": CONFIG["label"]})

    async def submit(self, request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Choose a photo and describe its motion.")
        source_id = payload.get("source_id")
        source = self.store.one("SELECT id,width,height FROM uploads WHERE id=?", (source_id,)) if isinstance(source_id, str) else None
        if not source:
            raise ValueError("Choose an uploaded or saved image first.")
        recipe = validate_recipe(payload, (source["width"], source["height"]))
        try:
            status = await self.native("GET", "/video/status")
        except (OSError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            return web.json_response({"error": str(error)}, status=503)
        if not status.get("ready"):
            return web.json_response({"error": status.get("error", "Install the video model first.")}, status=503)
        if status.get("sizing_version", 0) < 2:
            return web.json_response({"error": "Restart the Mac helper to enable photo-sized videos."}, status=503)
        async with self.submission_lock:
            if self.store.one("SELECT COUNT(*) AS n FROM videos WHERE status IN ('queued','running')")["n"] >= 4:
                return web.json_response({"error": "Finish or cancel a video before adding more."}, status=429)
            video_id = str(uuid.uuid4())
            recipe.update(source_id=source_id, id=video_id)
            # Independent source survives later image/project deletion.
            source_path = media_file(self.store.directory, "uploads", source_id)
            try:
                save_video_source(source_path, self.directory / (video_id + ".png"), recipe)
                self.store.execute("INSERT INTO videos (id,created,status,recipe) VALUES (?,?,?,?)",
                                   (video_id, time.time(), "queued", json.dumps(recipe)))
            except BaseException:
                (self.directory / (video_id + ".png")).unlink(missing_ok=True)
                raise
        self.wake.set()
        return web.json_response({"id": video_id}, status=202)

    def entries(self):
        rows = self.store.rows("SELECT * FROM videos ORDER BY created DESC LIMIT 100")
        for row in rows:
            row["recipe"] = json.loads(row["recipe"])
            row["url"] = f'/media/video/{row["id"]}' if row["status"] == "complete" else None
        return rows

    async def library(self, request):
        return web.json_response({"videos": self.entries()})

    async def delete(self, request):
        video_id = request.match_info["id"]
        row = self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))
        if not row:
            raise web.HTTPNotFound()
        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("confirm") is not True:
            return web.json_response({"error": "Confirm deletion of this video first."}, status=400)
        if row["status"] in ("queued", "running"):
            return web.json_response({"error": "Stop the video first, or wait for it to finish."}, status=409)
        self.store.execute("DELETE FROM videos WHERE id=?", (video_id,))
        for suffix in (".mp4", ".png"):
            (self.directory / (video_id + suffix)).unlink(missing_ok=True)
        return web.json_response({"ok": True})

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

#!/usr/bin/env python3
"""Authenticated local SAM 3 helper; each inference is an isolated process."""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.automask import validate_selection  # noqa: E402

CONFIG = json.loads((ROOT / "config/sam3.json").read_text())
TOKEN = (ROOT / "runtime/sam3-token.txt").read_text().strip()
BINARY = ROOT / "runtime/sam3-build/studio-sam3"
MODEL = ROOT / "models/sam3" / CONFIG["filename"]
LOCK = asyncio.Lock()
CACHE = OrderedDict()


@web.middleware
async def authorize(request, handler):
    if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + TOKEN):
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        return await handler(request)
    except (ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return web.json_response({"error": "Choose a valid image and short object name."}, status=400)


async def health(request):
    return web.json_response({"ready": BINARY.is_file() and MODEL.is_file(), "busy": LOCK.locked(),
                              "model": "SAM 3 Q8 · Metal"})


async def engine_busy():
    # Refuse to compete with native ComfyUI jobs, including ones outside the website.
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get(os.environ.get("COMFY_URL", "http://127.0.0.1:8188") + "/queue") as response:
                if response.status != 200:
                    return True
                queue = await response.json()
                return bool(queue.get("queue_running") or queue.get("queue_pending"))
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return True


async def select(request):
    reader = await request.multipart()
    content = None
    fields = {}
    async for part in reader:
        if part.name == "image":
            chunks = bytearray()
            while chunk := await part.read_chunk():
                chunks.extend(chunk)
                if len(chunks) > 5 * 1024 * 1024:
                    raise ValueError("Image too large")
            content = bytes(chunks)
        elif part.name in ("target", "protect_faces", "protect_skin"):
            value = bytearray()
            while chunk := await part.read_chunk():
                value.extend(chunk)
                if len(value) > 160:
                    raise ValueError("Object name too long")
            fields[part.name] = value.decode("utf-8")
        else:
            raise ValueError("Unknown field")
    target, protect, protect_skin = validate_selection({"target": fields.get("target", ""),
                                                        "protect_faces": fields.get("protect_faces") == "1",
                                                        "protect_skin": fields.get("protect_skin") == "1"})
    with Image.open(io.BytesIO(content or b"")) as image:
        if image.format != "PNG" or max(image.size) > 1024 or min(image.size) < 1:
            raise ValueError("Invalid image")
        image.verify()
    key = hashlib.sha256(content + target.encode() + bytes([protect,protect_skin])).hexdigest()
    cached = CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 3600:
        CACHE.move_to_end(key)
        return web.json_response(dict(cached[1], cached=True))
    if LOCK.locked() or await engine_busy():
        return web.json_response({"error": "Your Mac is making an image. Try automatic selection when it finishes."}, status=409)
    if LOCK.locked():
        return web.json_response({"error": "Automatic selection is already running. Try again when it finishes."}, status=409)
    async with LOCK:
        with tempfile.TemporaryDirectory(prefix="studio-sam3-", dir=ROOT / "runtime") as directory:
            folder = Path(directory)
            path = folder / "photo.png"
            path.write_bytes(content)
            with (folder / "inference.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(str(BINARY), str(MODEL), str(path), str(folder), target,
                                                               "1" if protect else "0", "1" if protect_skin else "0", stdout=log, stderr=log)
                try:
                    code = await asyncio.wait_for(process.wait(), timeout=170)
                    if code:
                        return web.json_response({"error": "SAM 3 could not select this image. Use the brush, or try again."}, status=503)
                    payload = json.loads((folder / "result.json").read_text())
                    for kind in ("candidates", "faces", "skin"):
                        for row in payload[kind]:
                            # File names are produced only by our fixed native wrapper.
                            row["png"] = base64.b64encode((folder / row.pop("file")).read_bytes()).decode("ascii")
                    payload.update(model="SAM 3 Q8", model_revision=CONFIG["model_revision"], cached=False)
                    CACHE[key] = (time.monotonic(), payload)
                    while len(CACHE) > 8:
                        CACHE.popitem(last=False)
                    return web.json_response(payload)
                except asyncio.TimeoutError:
                    return web.json_response({"error": "Automatic selection took too long. Try the brush."}, status=503)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()


if __name__ == "__main__":
    app = web.Application(middlewares=[authorize], client_max_size=6 * 1024 * 1024)
    app.add_routes([web.get("/health", health), web.post("/select", select)])
    web.run_app(app, host="127.0.0.1", port=7877, access_log=None, print=None, handler_cancellation=True)

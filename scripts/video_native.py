"""Native MLX video endpoint for the authenticated Mac helper."""
import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path

from aiohttp import web
from PIL import Image, ImageOps

from studio.video import CONFIG, validate_recipe


class NativeVideo:
    def __init__(self, root, engine, resource_lock, engine_busy):
        self.root, self.engine, self.lock = Path(root), engine, resource_lock
        self.engine_busy = engine_busy
        self.busy = False
        self.executable = self.root / "runtime/ltx-2-mlx/.venv/bin/ltx-2-mlx"

    def environment(self):
        return dict(os.environ, PATH=os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", "")]),
                    HF_HOME=str(self.root / ".cache/huggingface"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

    def ready(self):
        marker = self.root / "runtime/video-ready.json"
        try:
            installed = json.loads(marker.read_text()) == CONFIG
        except (OSError, ValueError):
            installed = False
        models = self.root / "models"
        files = all((models / CONFIG["model"]["directory"] / name).is_file() for name in CONFIG["model"]["files"])
        return (installed and files and self.executable.is_file()
                and (models / CONFIG["text_encoder"]["directory"] / "model.safetensors.index.json").is_file()
                and bool(shutil.which("ffmpeg", path=self.environment()["PATH"]))
                and bool(shutil.which("ffprobe", path=self.environment()["PATH"])))

    async def status(self, request):
        ready = self.ready()
        return web.json_response({"ready": ready, "busy": self.busy,
                                  "error": None if ready else "On your Mac, run: .venv/bin/python scripts/setup_video.py"})

    def command(self, recipe, source, output):
        return [str(self.executable), "generate", "--distilled", "--low-ram",
                "--model", str(self.root / "models" / CONFIG["model"]["directory"]),
                "--gemma", str(self.root / "models" / CONFIG["text_encoder"]["directory"]),
                "--prompt", recipe["prompt"], "--image", str(source),
                "--height", str(recipe["height"]), "--width", str(recipe["width"]),
                "--frames", str(recipe["frames"]), "--frame-rate", str(recipe["fps"]),
                "--seed", str(recipe["seed"]), "--output", str(output)]

    async def generate(self, request):
        if not self.ready():
            return web.json_response({"error": "Install the video runtime and weights using scripts/setup_video.py."}, status=503)
        if self.lock.locked():
            return web.json_response({"error": "Your Mac is busy. Try again after the current task finishes."}, status=409)
        # Hold both locks through execution: waking ComfyUI and SAM inference wait/refuse.
        async with self.lock, self.engine.lock:
            if await self.engine_busy():
                return web.json_response({"error": "Finish the current ComfyUI queue before making a video."}, status=409)
            state = await self.engine.state()
            if state != "sleeping" and not self.engine.process:
                return web.json_response({"error": "Close the manually started ComfyUI engine before making a video."}, status=409)
            self.busy = True
            try:
                reader = await request.multipart()
                fields = {}
                async for part in reader:
                    if part.name not in ("recipe", "image") or part.name in fields:
                        raise ValueError("Invalid video input.")
                    content = bytearray()
                    limit = 20 * 1024 * 1024 if part.name == "image" else 16384
                    while chunk := await part.read_chunk():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise ValueError("Video input is too large.")
                    fields[part.name] = bytes(content)
                if set(fields) != {"recipe", "image"}:
                    raise ValueError("A photo and motion description are required.")
                recipe = validate_recipe(json.loads(fields["recipe"]))
                with tempfile.TemporaryDirectory(prefix="studio-video-", dir=self.root / "runtime") as directory:
                    folder = Path(directory)
                    source, output = folder / "source.png", folder / "result.mp4"
                    with Image.open(io.BytesIO(fields["image"])) as photo:
                        ImageOps.exif_transpose(photo).convert("RGB").save(source)
                    await self.engine.stop()
                    log_path = self.root / "runtime/video-inference.log"
                    with log_path.open("wb") as log:
                        process = await asyncio.create_subprocess_exec(*self.command(recipe, source, output),
                            cwd=self.root, env=self.environment(), stdout=log, stderr=log,
                            stdin=asyncio.subprocess.DEVNULL)
                        try:
                            code = await asyncio.wait_for(process.wait(), 7100)
                        finally:
                            if process.returncode is None:
                                with contextlib.suppress(ProcessLookupError):
                                    process.kill()
                                await process.wait()
                    if code or not output.is_file():
                        return web.json_response({"error": "LTX could not finish this video. Check runtime/video-inference.log on your Mac."}, status=503)
                    # Stream before removing the temporary directory. FileResponse defers reading.
                    response = web.StreamResponse(headers={"Content-Type": "video/mp4", "Content-Length": str(output.stat().st_size)})
                    await response.prepare(request)
                    with output.open("rb") as video:
                        while chunk := video.read(1024 * 1024):
                            await response.write(chunk)
                    await response.write_eof()
                    return response
            except asyncio.TimeoutError:
                return web.json_response({"error": "Video generation exceeded its time limit. Try a shorter clip."}, status=503)
            finally:
                self.busy = False

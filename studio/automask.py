"""Client for the separate native Mac segmentation service."""
import asyncio
import io
import os
from pathlib import Path

import aiohttp
from PIL import Image


class AutomaticSelectionError(ValueError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status=status


def validate_selection(payload):
    if not isinstance(payload, dict):
        raise ValueError("Choose an object and face protection setting.")
    target = payload.get("target", "")
    protect = payload.get("protect_faces", True)
    skin = payload.get("protect_skin", False)
    if not isinstance(target, str) or len(target) > 80 or any(ord(c) < 32 for c in target):
        raise ValueError("Describe the object with a short name, such as shirt or hand.")
    if type(protect) is not bool or type(skin) is not bool:
        raise ValueError("Choose whether to protect detected faces and visible skin.")
    target = target.strip()
    if not target and not protect and not skin:
        raise ValueError("Enter an object to find or enable face protection.")
    return target, protect, skin


def selection_image(path):
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()


class AutoMask:
    def __init__(self):
        root = Path(__file__).resolve().parents[1]
        self.base = os.environ.get("SAM3_URL", "http://127.0.0.1:7877").rstrip("/")
        self.token_file = Path(os.environ.get("SAM3_TOKEN_FILE", root / "runtime/sam3-token.txt"))

    def headers(self):
        return {"Authorization": "Bearer " + self.token_file.read_text().strip()}

    async def status(self):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(self.base + "/health", headers=self.headers()) as response:
                    if response.status == 200:
                        return await response.json()
        except (OSError, aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return {"ready": False, "busy": False}

    async def select(self, path, target, protect, protect_skin=False, concepts=()):
        try:
            content = await asyncio.to_thread(selection_image, path)
            form = aiohttp.FormData()
            form.add_field("image", content, filename="photo.png", content_type="image/png")
            form.add_field("target", target)
            form.add_field("protect_faces", "1" if protect else "0")
            form.add_field("protect_skin", "1" if protect_skin else "0")
            form.add_field("concepts", ",".join(concepts))
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
                async with session.post(self.base + "/select", data=form, headers=self.headers()) as response:
                    payload = await response.json()
                    if response.status != 200:
                        raise AutomaticSelectionError(payload.get("error", "Automatic selection could not finish. Try the brush."),response.status)
                    return payload
        except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise AutomaticSelectionError("Automatic selection is unavailable. Open Start Web Studio on your Mac, or use the brush.") from error

"""Short-lived ZIP downloads, streamed from disk rather than held in phone memory."""
import asyncio
import contextlib
import tempfile
import time
import uuid
import zipfile
from pathlib import Path


class Downloads:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-downloads-")
        self.entries = {}
        self.lock = asyncio.Lock()

    def prune(self):
        for token, entry in list(self.entries.items()):
            if entry["expires"] < time.time():
                entry["path"].unlink(missing_ok=True)
                del self.entries[token]

    async def create(self, files, image_format, jpeg_encoder):
        token = uuid.uuid4().hex
        path = Path(self.temporary.name) / (token + ".zip")
        def build():
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, source in files:
                    if image_format == "jpg":
                        archive.writestr(name + ".jpg", jpeg_encoder(source))
                    else:
                        archive.write(source, name + ".png")
        try:
            async with self.lock:
                self.prune()
                await asyncio.to_thread(build)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.entries[token] = {"path": path, "expires": time.time() + 600}
        return {"url": "/api/downloads/" + token, "images": len(files), "filename": "studio-images.zip"}

    def get(self, token):
        self.prune()
        return self.entries.get(token)

    async def context(self, app):
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                self.prune()
        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self.temporary.cleanup()

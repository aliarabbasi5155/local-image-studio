"""Authenticated control of the small Mac helper (also used by Docker)."""
import asyncio
import contextlib
import os
from pathlib import Path

import aiohttp


class NativeEngine:
    def __init__(self):
        self.base = os.environ.get("ENGINE_CONTROL_URL", "").rstrip("/")
        root = Path(__file__).resolve().parents[1]
        self.token_file = Path(os.environ.get("SAM3_TOKEN_FILE", root / "runtime/sam3-token.txt"))

    async def request(self, wake=False):
        if not self.base:
            return None
        try:
            headers = {"Authorization": "Bearer " + self.token_file.read_text().strip()}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.request("POST" if wake else "GET", self.base + ("/engine/wake" if wake else "/engine"),
                                           headers=headers) as response:
                    payload = await response.json()
                    if response.status != 200:
                        raise RuntimeError(payload.get("error", "Your Mac’s helper is unavailable."))
                    return payload
        except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise RuntimeError("Your Mac’s background helper is unavailable. Make sure you are logged in on your Mac, then try again.") from error

    async def heartbeat(self):
        while True:
            await asyncio.sleep(30)
            try:
                await self.request(wake=True)
            except (OSError, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
                # The helper also checks ComfyUI's queue before it can stop it.
                pass

    @contextlib.asynccontextmanager
    async def activity(self):
        task = asyncio.create_task(self.heartbeat()) if self.base else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

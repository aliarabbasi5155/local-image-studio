"""Own the native image process, waking it on demand and stopping it when idle."""
import asyncio
import contextlib
import os
import time
from pathlib import Path

import aiohttp
import psutil


# Set in the environment of the engine this helper starts, so a restarted helper can find it again.
MARKER = "STUDIO_ENGINE_OWNER"


class AdoptedProcess:
    """An engine this helper started before it last restarted, controlled like a fresh child."""
    def __init__(self, process):
        self.process = process

    @property
    def returncode(self):
        try:
            return None if self.process.status() != psutil.STATUS_ZOMBIE else 0
        except psutil.NoSuchProcess:
            return 0

    def terminate(self):
        with contextlib.suppress(psutil.NoSuchProcess):
            self.process.terminate()

    def kill(self):
        with contextlib.suppress(psutil.NoSuchProcess):
            self.process.kill()

    async def wait(self):
        with contextlib.suppress(psutil.NoSuchProcess):
            await asyncio.to_thread(self.process.wait)


class EngineSupervisor:
    def __init__(self, root, idle_seconds=600):
        self.root = Path(root)
        self.idle_seconds = idle_seconds
        self.base = "http://127.0.0.1:8188"
        self.process = None
        self.lock = asyncio.Lock()
        self.last_activity = time.monotonic()
        self.started = None
        self.error = None
        self.session = None
        self.searched = 0

    async def probe(self, path):
        async with self.session.get(self.base + path) as response:
            response.raise_for_status()
            return await response.json()

    async def state(self):
        if self.process and self.process.returncode is not None:
            self.error = "The image engine stopped unexpectedly. Try submitting the image again."
            self.process = None
        try:
            stats = await self.probe("/system_stats")
            if str(self.root / "outputs") not in stats.get("system", {}).get("argv", []):
                return "unavailable"
            self.started = None
            if not self.process and time.monotonic() - self.searched > 30:
                self.searched = time.monotonic()
                found = await asyncio.to_thread(self.find_own_engine)
                if found:
                    self.process = AdoptedProcess(found)
            return "ready"
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return ("starting" if self.started is not None else "unresponsive") if self.process else "sleeping"

    def find_own_engine(self):
        """The ComfyUI this helper started before a restart. One started by hand has no marker and stays its own."""
        outputs = str(self.root / "outputs")
        for process in psutil.process_iter(["cmdline", "environ"]):
            if outputs in (process.info["cmdline"] or []) and (process.info["environ"] or {}).get(MARKER) == "helper":
                return process
        return None

    async def status(self):
        state = await self.state()
        memory = psutil.virtual_memory()
        return {"state": state, "managed": self.process is not None, "auto_start": True,
                "idle_seconds": self.idle_seconds, "error": self.error,
                "memory_total": memory.total, "memory_free": memory.available}

    async def wake(self):
        async with self.lock:
            self.last_activity = time.monotonic()
            state = await self.state()
            if state == "unavailable":
                raise RuntimeError("Another image engine is using port 8188. Close it and try again.")
            if state == "sleeping":
                if self.error:
                    error, self.error = self.error, None
                    raise RuntimeError(error)
                self.error = None
                with (self.root / "runtime/comfyui-launcher.log").open("a") as log:
                    self.process = await asyncio.create_subprocess_exec(
                        str(self.root / "scripts/start.sh"), cwd=self.root, env={**os.environ, MARKER: "helper"},
                        stdout=log, stderr=log, stdin=asyncio.subprocess.DEVNULL)
                self.started = time.monotonic()
            return await self.status()

    async def stop(self):
        # Only stop the exact child we started, never a PID read from a stale file.
        process, self.process = self.process, None
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 20)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def check_idle(self):
        async with self.lock:
            if not self.process:
                return
            state = await self.state()
            if state == "starting":
                if time.monotonic() - self.started > 120:
                    await self.stop()
                    self.error = "The image engine could not start. Check runtime/comfyui-launcher.log."
                return
            if state != "ready":
                return
            try:
                queue = await self.probe("/queue")
                # Missing/unreadable queue data must never be treated as idle.
                busy = queue["queue_running"] or queue["queue_pending"]
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
                self.last_activity = time.monotonic()
                return
            if busy:
                self.last_activity = time.monotonic()
            elif time.monotonic() - self.last_activity >= self.idle_seconds:
                await self.stop()

    async def monitor(self):
        while True:
            await asyncio.sleep(10)
            await self.check_idle()

    async def context(self, app):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            self.session = session
            monitor = asyncio.create_task(self.monitor())
            try:
                yield
            finally:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
                await self.stop()

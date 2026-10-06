"""Honest duration ranges learned from completed local jobs and host memory."""
import asyncio
import json
import math
import shutil
import statistics
import time
import aiohttp


class Metrics:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.started = time.time()
        self.cached, self.checked = {}, 0
        self.lock = asyncio.Lock()

    def history(self):
        return [dict(row, recipe=json.loads(row["recipe"])) for row in self.store.rows(
            "SELECT recipe,finished-started AS seconds FROM jobs WHERE status='complete' AND finished>started AND started IS NOT NULL ORDER BY finished DESC LIMIT 100")]

    def estimate(self, recipe):
        pixels = recipe.get("generation_width",recipe["width"]) * recipe.get("generation_height",recipe["height"])
        samples = []
        for row in self.history():
            previous = row["recipe"]
            if previous["mode"] != recipe["mode"] or previous["model"] != recipe["model"]:
                continue
            previous_pixels = previous.get("generation_width",previous["width"]) * previous.get("generation_height",previous["height"])
            ratio = pixels / previous_pixels
            method_matches = previous.get("processing",{}).get("method") == recipe.get("processing",{}).get("method")
            distance = abs(math.log2(ratio)) + (0 if method_matches else 1)
            samples.append((distance, row["seconds"] * ratio**1.5))
        if not samples:
            return {"seconds":None,"low":None,"high":None,"samples":0,"approximate":True,"reason":"Learning from your first completed images"}
        samples.sort(key=lambda sample:sample[0])
        nearest = samples[:5]
        seconds = max(1,round(statistics.median(sample[1] for sample in nearest)))
        uncertainty = .35 if nearest[0][0] < .5 and len(nearest) >= 3 else .65
        return {"seconds":seconds,"low":max(1,round(seconds*(1-uncertainty))),"high":round(seconds*(1+uncertainty)),
                "samples":len(nearest),"approximate":True,
                "reason":"Based on similar completed images" if nearest[0][0] < .5 else "Rough estimate; this size or processing mode has fewer matching runs"}

    def queue(self, jobs=None):
        if jobs is None:
            jobs = [self.store.job(row["id"]) for row in self.store.rows("SELECT id FROM jobs WHERE status IN ('queued','submitted','running') ORDER BY created,variation")]
        wait_low, wait_high = 0, 0
        now = time.time()
        result = {}
        for position, job in enumerate(jobs):
            estimate = self.estimate(job["recipe"])
            elapsed = max(0,now-job["started"]) if job["started"] else 0
            remaining_low = max(0,estimate["low"]-elapsed) if estimate["low"] is not None else None
            remaining_high = max(0,estimate["high"]-elapsed) if estimate["high"] is not None else None
            overdue = remaining_high == 0 and bool(elapsed)
            result[job["id"]] = dict(estimate, queue_position=position, elapsed_seconds=round(elapsed),
                                     remaining_low=wait_low+remaining_low if wait_low is not None and remaining_low is not None and not overdue else None,
                                     remaining_high=wait_high+remaining_high if wait_high is not None and remaining_high is not None and not overdue else None,
                                     overdue=overdue)
            wait_low = wait_low+remaining_low if wait_low is not None and remaining_low is not None and not overdue else None
            wait_high = wait_high+remaining_high if wait_high is not None and remaining_high is not None and not overdue else None
        return result

    async def status(self):
        async with self.lock:
            if time.monotonic()-self.checked > 5:
                started = time.monotonic()
                try:
                    stats = await asyncio.wait_for(self.engine.request("GET","/system_stats"),3)
                    system = stats.get("system",{})
                    self.cached = {"engine_ready":True,"memory_total":system.get("ram_total"),"memory_free":system.get("ram_free"),
                                   "engine_response_ms":round((time.monotonic()-started)*1000)}
                except (OSError,RuntimeError,AttributeError,asyncio.TimeoutError,aiohttp.ClientError):
                    self.cached = {"engine_ready":False,"memory_total":None,"memory_free":None,"engine_response_ms":None}
                self.checked = time.monotonic()
        jobs = self.store.rows("SELECT status FROM jobs WHERE status IN ('queued','submitted','running')")
        history = self.history()
        return dict(self.cached, active_images=len(jobs), queued_images=sum(job["status"] == "queued" for job in jobs),
                    last_image_seconds=round(history[0]["seconds"]) if history else None,
                    recent_average_seconds=round(statistics.mean(row["seconds"] for row in history[:10])) if history else None,
                    storage_free=shutil.disk_usage(self.store.directory).free, uptime_seconds=round(time.time()-self.started))

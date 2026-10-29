#!/usr/bin/env python3
"""Generate one benign video through Studio and record a real end-to-end check."""
import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path

import aiohttp
from yarl import URL

ROOT = Path(__file__).resolve().parents[1]


async def check(args):
    started = time.monotonic()
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), timeout=aiohttp.ClientTimeout(total=30)) as session:
        async def request(method, path, **kwargs):
            async with session.request(method, args.url.rstrip("/") + path, **kwargs) as response:
                payload = await response.json()
                if response.status >= 400:
                    raise RuntimeError(payload.get("error", str(response.status)))
                return payload
        await request("POST", "/api/login", json={"pin": args.pin_file.read_text().strip()})
        options = await request("GET", "/api/videos/options")
        if not options.get("ready"):
            raise RuntimeError(options.get("error", "Video setup is not ready."))
        with args.image.open("rb") as source:
            form = aiohttp.FormData()
            form.add_field("image", source, filename=args.image.name, content_type="image/png")
            image = await request("POST", "/api/uploads", data=form)
        submitted = await request("POST", "/api/videos", json={"source_id": image["id"], "prompt": args.prompt,
            "output_size": args.output_size, "frames": args.frames, "seed": 42})
        video_id = submitted["id"]
        print("Video job:", video_id, flush=True)
        deadline = time.monotonic() + 7200
        while True:
            jobs = await request("GET", "/api/videos")
            row = next(row for row in jobs["videos"] if row["id"] == video_id)
            if row["status"] in ("failed", "cancelled"):
                raise RuntimeError(row.get("error") or row["status"])
            if row["status"] == "complete":
                break
            if time.monotonic() > deadline:
                await request("POST", f"/api/videos/{video_id}/cancel", json={})
                raise TimeoutError("Video check timed out.")
            await asyncio.sleep(3)
        output = ROOT / "outputs" / ("video-check-" + video_id + ".mp4")
        async with session.get(args.url.rstrip("/") + row["url"]) as response:
            response.raise_for_status()
            with output.open("wb") as file:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    file.write(chunk)
        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], text=True))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        assert (video["width"], video["height"]) == (row["recipe"]["width"], row["recipe"]["height"]), video
        assert video["width"] * image["height"] == video["height"] * image["width"], video
        assert abs(float(probe["format"]["duration"]) - args.frames / 24) < .15, probe
        assert int(video["nb_frames"]) == args.frames, video
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"], check=True)
        report = {"job": video_id, "elapsed_seconds": round(time.monotonic() - started, 2),
                  "output": str(output.relative_to(ROOT)), "recipe": row["recipe"], "probe": probe}
        report_path = args.report
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"output": str(output), "report": str(report_path), "elapsed_seconds": report["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    parser.add_argument("--pin-file", type=Path, default=ROOT / "studio-data/access-pin.txt")
    parser.add_argument("--image", type=Path, default=ROOT / "examples/flux2-klein-9b-8bit.png")
    parser.add_argument("--frames", type=int, choices=(49, 97, 145), default=49)
    parser.add_argument("--output-size", choices=("original", "preview"), default="original")
    parser.add_argument("--report", type=Path, default=ROOT / "benchmarks/video-check.json")
    parser.add_argument("--prompt", default="A quiet lakeside village in warm morning light. The wooden boat rocks gently on small ripples. Wisteria flowers sway softly in the breeze. The camera stays still. Natural subtle movement.")
    arguments = parser.parse_args()
    if URL(arguments.url).host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Use the local Studio address for this check.")
    asyncio.run(check(arguments))

"""Translate studio recipes into the existing ComfyUI workflows."""
import asyncio
import contextlib
import copy
import json
import time
import uuid
from pathlib import Path

import aiohttp
from PIL import Image, ImageOps

QUALITIES = {
    "fast": {"label": "Fast", "description": "Quick previews", "model": "flux2-klein-9b-4bit",
             "sizes": {"square": [512, 512], "portrait": [512, 640], "landscape": [768, 512]}},
    "balanced": {"label": "Balanced", "description": "Everyday detail", "model": "flux2-klein-9b-8bit",
                 "sizes": {"square": [768, 768], "portrait": [640, 800], "landscape": [960, 640]}},
    "high": {"label": "High", "description": "More pixels, more time", "model": "flux2-klein-9b-8bit",
             "sizes": {"square": [1024, 1024], "portrait": [832, 1040], "landscape": [1152, 768]}},
}


def build_graph(recipe, project_root, templates, upload_name=None):
    editing = recipe["mode"] == "edit"
    generation_width = recipe.get("generation_width", recipe["width"])
    generation_height = recipe.get("generation_height", recipe["height"])
    processing = recipe.get("processing", {"method": "none"})
    processed = processing["method"] != "none"
    model = recipe["model_source"]
    prefix = "web-studio/" + recipe["image_id"]
    loader = {
        "model_name": model["model_name"], "quantize": model["quantize"],
        "custom_model_path": str(Path(project_root) / "models" / recipe["model"]),
        "loras": "SNOFS/klein_snofs_v1_4.safetensors:1.0" if recipe["snofs"] else "",
    }
    inputs = {
        "pipeline": ["1", 0], "prompt": recipe["prompt"], "width": generation_width,
        "height": generation_height, "seed": recipe["seed"], "num_inference_steps": 4,
        "guidance": 1.0, "filename_prefix": prefix + "/base" if processed else prefix,
    }
    graph = {
        "1": {"class_type": "Flux2KleinEditModelLoader" if editing else "Flux2KleinModelLoader", "inputs": loader},
        "2": {"class_type": "Flux2KleinEdit" if editing else "Flux2KleinGenerate", "inputs": inputs},
    }
    workflow = copy.deepcopy(templates[recipe["mode"]])
    nodes = {n["id"]: n for n in workflow["nodes"]}
    nodes[1]["widgets_values"] = [loader["model_name"], loader["quantize"], loader["custom_model_path"], loader["loras"]]
    values = [generation_height, generation_width, 4, recipe["seed"], "fixed", 1.0, inputs["filename_prefix"]]
    if editing:
        inputs.update(image=["5", 0], image_paths="", use_kv_cache=False)
        graph["5"] = {"class_type": "LoadImage", "inputs": {"image": upload_name}}
        nodes[5]["widgets_values"] = [upload_name, "image"]
        nodes[5]["widgets_values_named"] = {"image": upload_name, "upload": "image"}
        nodes[2]["widgets_values"] = [recipe["prompt"], "", *values, False]
    else:
        inputs["image_strength"] = 0.3
        nodes[2]["widgets_values"] = [recipe["prompt"], *values, 0.3]
    if processed:
        append_processing(graph, workflow, recipe, prefix)
    return graph, workflow


def append_processing(graph, workflow, recipe, prefix):
    """Embed a runnable ComfyUI workflow for the final-size PNG, too."""
    nodes = {n["id"]: n for n in workflow["nodes"]}
    preview_link = nodes[3]["inputs"][0]["link"]
    workflow["links"] = [link for link in workflow["links"] if link[0] != preview_link]
    nodes[2]["outputs"][0]["links"] = []
    next_link = max((link[0] for link in workflow["links"]), default=0) + 1

    def node(node_id, kind, position, widgets, input_specs, output_type="IMAGE"):
        result = {"id": node_id, "type": kind, "pos": position, "size": [320, 160],
                  "flags": {}, "order": len(nodes), "mode": 0,
                  "inputs": [{"name": name, "type": kind, "link": None} for name, kind in input_specs],
                  "outputs": [{"name": output_type, "type": output_type, "slot_index": 0, "links": []}],
                  "properties": {"Node name for S&R": kind}, "widgets_values": widgets}
        workflow["nodes"].append(result); nodes[node_id] = result

    def connect(origin, target, target_slot, kind="IMAGE"):
        nonlocal next_link
        workflow["links"].append([next_link, origin, 0, target, target_slot, kind])
        nodes[origin]["outputs"][0]["links"].append(next_link)
        nodes[target]["inputs"][target_slot]["link"] = next_link
        next_link += 1

    image_node = "2"
    if recipe["processing"]["method"] == "ai_upscale":
        filename = recipe["processing"]["upscaler"]["filename"]
        graph["6"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": filename}}
        graph["7"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["6", 0], "image": ["2", 0]}}
        node(6, "UpscaleModelLoader", [940, 700], [filename], [], "UPSCALE_MODEL")
        node(7, "ImageUpscaleWithModel", [1300, 400], [], [("upscale_model", "UPSCALE_MODEL"), ("image", "IMAGE")])
        connect(6, 7, 0, "UPSCALE_MODEL"); connect(2, 7, 1)
        image_node = "7"
    graph["8"] = {"class_type": "ImageScale", "inputs": {"image": [image_node, 0], "upscale_method": "lanczos",
                   "width": recipe["width"], "height": recipe["height"], "crop": "disabled"}}
    graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix + "/final"}}
    node(8, "ImageScale", [1660, 400], ["lanczos", recipe["width"], recipe["height"], "disabled"], [("image", "IMAGE")])
    node(9, "SaveImage", [2020, 400], [prefix + "/final"], [("images", "IMAGE")])
    connect(int(image_node), 8, 0); connect(8, 9, 0); connect(8, 3, 0)
    nodes[3]["pos"] = [2380, 400]
    workflow["last_node_id"] = 9; workflow["last_link_id"] = next_link - 1


def make_thumbnail(source, destination):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((640, 640))
        image.save(destination, "WEBP", quality=85)


class Engine:
    def __init__(self, store, base_url, project_root, templates):
        self.store = store
        self.base = base_url.rstrip("/")
        self.project_root = project_root
        self.templates = templates
        self.wake = asyncio.Event()
        self.client_id = "studio-" + uuid.uuid4().hex
        self.session = None

    async def request(self, method, path, **kwargs):
        async with self.session.request(method, self.base + path, **kwargs) as response:
            payload = await response.json()
            if response.status >= 400:
                raise RuntimeError(f"Image engine rejected the request: {payload.get('error', payload)}")
            return payload

    async def available(self):
        try:
            await self.request("GET", "/system_stats")
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            return False

    async def cancel(self, job):
        if job["prompt_id"]:
            await self.request("POST", f"/api/jobs/{job['prompt_id']}/cancel", json={})

    async def watch(self, ws, job_id, prompt_id):
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            data = event.get("data", {})
            if data.get("prompt_id") != prompt_id:
                continue
            if event["type"] == "executing":
                node = str(data.get("node"))
                if node == "1":
                    self.store.update_job(job_id, status="running", phase="Preparing your image")
                elif node == "2":
                    self.store.update_job(job_id, status="running", phase="Creating your image")
                elif node in ("6", "7"):
                    self.store.update_job(job_id, status="running", phase="Enhancing image detail", progress=0.65)
                elif node == "8":
                    self.store.update_job(job_id, status="running", phase="Fitting your output size", progress=0.95)
                elif node == "9":
                    self.store.update_job(job_id, phase="Saving your image", progress=0.98)
            elif event["type"] == "progress":
                enhancing = self.store.job(job_id)["phase"] == "Enhancing image detail"
                fraction = data["value"] / max(1, data["max"])
                processed = self.store.job(job_id)["recipe"].get("processing", {}).get("method", "none") != "none"
                self.store.update_job(job_id, status="running", phase="Enhancing image detail" if enhancing else "Creating your image",
                                      progress=min(0.95, .65 + .3 * fraction if enhancing else fraction * (.6 if processed else .95)))

    async def finish(self, job, history):
        recipe = job["recipe"]
        status = history.get("status", {})
        if status.get("status_str") != "success":
            events = dict(status.get("messages", []))
            error = events.get("execution_error", {}).get("exception_message", "The image engine stopped before finishing.")
            self.store.update_job(job["id"], status="cancelled" if job["cancel_requested"] else "failed",
                                  phase="Stopped", error=error[:1500], finished=time.time())
            return
        output_node = "9" if recipe.get("processing", {}).get("method", "none") != "none" else "2"
        outputs = history.get("outputs", {}).get(output_node, {}).get("images", [])
        output = next((x for x in outputs if x.get("type") == "output"), None)
        if not output:
            raise RuntimeError("The image engine returned no saved image.")
        self.store.update_job(job["id"], phase="Saving your image", progress=0.98)
        async with self.session.get(self.base + "/view", params=output) as response:
            response.raise_for_status()
            content = await response.read()
        path = self.store.directory / "images" / (job["id"] + ".png")
        temporary = path.with_suffix(".part")
        temporary.write_bytes(content)
        with Image.open(temporary) as image:
            image.verify()
        with Image.open(temporary) as image:
            if image.size != (recipe["width"], recipe["height"]):
                raise RuntimeError("The image engine returned an unexpected image size.")
            if json.loads(image.info.get("studio", "{}")) != recipe:
                raise RuntimeError("The image is missing its saved generation settings.")
        temporary.replace(path)
        await asyncio.to_thread(make_thumbnail, path, self.store.directory / "thumbs" / (job["id"] + ".webp"))
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO images (id,created,filename,recipe) VALUES (?,?,?,?)",
                                  (job["id"], time.time(), path.name, json.dumps(recipe)))
            self.store.db.execute("UPDATE jobs SET status='complete',phase='Complete',progress=1,finished=? WHERE id=?",
                                  (time.time(), job["id"]))

    async def run_job(self, job):
        prompt_id = job["prompt_id"]
        if prompt_id:
            history = await self.request("GET", "/history/" + prompt_id)
            queue = await self.request("GET", "/queue")
            known = any(row[1] == prompt_id for row in queue["queue_running"] + queue["queue_pending"])
            if prompt_id in history:
                await self.finish(job, history[prompt_id])
                return
            if not known:
                # A job may finish between the history and queue snapshots.
                history = await self.request("GET", "/history/" + prompt_id)
                if prompt_id in history:
                    await self.finish(job, history[prompt_id])
                    return
                raise RuntimeError("This job could not be recovered after a restart. Reuse its settings to try again.")
        async with self.session.ws_connect(self.base + "/ws", params={"clientId": self.client_id}, heartbeat=20) as ws:
            if not prompt_id:
                if job["recipe"]["mode"] == "edit":
                    source = self.store.directory / "uploads" / (job["recipe"]["source_id"] + ".png")
                    with source.open("rb") as file:
                        form = aiohttp.FormData()
                        form.add_field("image", file, filename=source.name, content_type="image/png")
                        form.add_field("subfolder", "web-studio")
                        form.add_field("overwrite", "true")
                        uploaded = await self.request("POST", "/upload/image", data=form)
                    upload_name = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                else:
                    upload_name = None
                graph, workflow = build_graph(job["recipe"], self.project_root, self.templates, upload_name)
                prompt_id = str(uuid.uuid4())
                # Persist identity before submitting; never duplicate an uncertain request.
                self.store.update_job(job["id"], prompt_id=prompt_id, status="submitted", started=time.time(), phase="Waiting for the image engine")
                await self.request("POST", "/prompt", json={
                    "prompt_id": prompt_id, "client_id": self.client_id, "prompt": graph,
                    "extra_data": {"extra_pnginfo": {"workflow": workflow, "studio": job["recipe"]}},
                })
            listener = asyncio.create_task(self.watch(ws, job["id"], prompt_id))
            try:
                deadline = time.monotonic() + 3600
                while time.monotonic() < deadline:
                    current = self.store.job(job["id"])
                    if current["cancel_requested"]:
                        await self.cancel(current)
                    history = await self.request("GET", "/history/" + prompt_id)
                    if prompt_id in history:
                        await self.finish(current, history[prompt_id])
                        return
                    if current["cancel_requested"]:
                        queue = await self.request("GET", "/queue")
                        if not any(row[1] == prompt_id for row in queue["queue_running"] + queue["queue_pending"]):
                            self.store.update_job(job["id"], status="cancelled", phase="Stopped", finished=time.time())
                            return
                    await asyncio.sleep(1)
                await self.cancel(self.store.job(job["id"]))
                raise RuntimeError("The image engine took over an hour. This job was stopped; you can reuse its settings.")
            finally:
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await listener

    async def worker(self):
        while True:
            row = self.store.one("SELECT id FROM jobs WHERE status IN ('queued','submitted','running') ORDER BY created,variation LIMIT 1")
            if not row:
                self.wake.clear()
                await self.wake.wait()
                continue
            job = self.store.job(row["id"])
            if job["cancel_requested"] and not job["prompt_id"]:
                self.store.update_job(job["id"], status="cancelled", phase="Stopped", finished=time.time())
                continue
            if not await self.available():
                self.store.update_job(job["id"], phase="Waiting for your Mac’s image engine")
                await asyncio.sleep(3)
                continue
            try:
                await self.run_job(job)
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, OSError, ValueError) as error:
                self.store.update_job(job["id"], status="failed", error=str(error)[:1500], phase="Needs attention", finished=time.time())

    async def context(self, app):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            self.session = session
            worker = asyncio.create_task(self.worker())
            yield
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

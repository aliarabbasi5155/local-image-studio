"""Translate studio recipes into the existing ComfyUI workflows."""
import asyncio
import contextlib
import copy
import io
import json
import time
import uuid
from pathlib import Path

import aiohttp
from PIL import Image, ImageOps
from .native import NativeEngine
from .masking import engine_mask, engine_reference, engine_placement_guide, engine_shape_guide, blend_selected, protect_unselected

QUALITIES = {
    "fast": {"label": "Fast", "description": "Quick previews", "model": "flux2-klein-9b-4bit",
             "sizes": {"square": [512, 512], "portrait": [512, 640], "landscape": [768, 512]}},
    "balanced": {"label": "Balanced", "description": "Everyday detail", "model": "flux2-klein-9b-8bit",
                 "sizes": {"square": [768, 768], "portrait": [640, 800], "landscape": [960, 640]}},
    "high": {"label": "High", "description": "More pixels, more time", "model": "flux2-klein-9b-8bit",
             "sizes": {"square": [1024, 1024], "portrait": [832, 1040], "landscape": [1152, 768]}},
}


def build_graph(recipe, project_root, templates, upload_name=None, mask_name=None, reference_name=None, guide_name=None):
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
    if recipe.get("masked_edit"):
        append_masked_processing(graph, workflow, recipe, prefix, mask_name, reference_name)
        if guide_name:
            paths=json.dumps([str(Path(project_root)/"runtime/input"/guide_name)])
            hint=guide_hint(recipe["masked_edit"])
            graph["2"]["inputs"].update(image_paths=paths,prompt=recipe["prompt"]+hint)
            nodes[2]["widgets_values"][0]=recipe["prompt"]+hint
            nodes[2]["widgets_values"][1]=paths
    elif processed:
        append_processing(graph, workflow, recipe, prefix)
    return graph, workflow


def guide_hint(selection):
    """Instructions for the second reference image; the user's prompt is never altered."""
    if selection.get("shape_guidance"):
        return (" The FIRST reference is the original photo to edit. The second reference is only a shape guide: "
                "the dark silhouette marks the exact outline of the area being changed in the original. "
                "Keep the person's body shape, proportions, pose and outline exactly as in the original photo, "
                "including the width of the shoulders, chest, waist and hips. The new result must fill that same silhouette, "
                "neither slimmer nor larger. Keep the original lighting, skin tone and background. "
                "Do not include any silhouette, outline, guide mark or gray overlay in the final photo.")
    placement=selection["selection"]["placement"]
    crop=selection["crop"]
    width_percent=round(100*(placement[2]-placement[0])/crop[2])
    height_percent=round(100*(placement[3]-placement[1])/crop[3])
    return (" The FIRST reference is the original photo to edit. The second reference is only a placement guide, with a small red dot marking the desired object's CENTER. "
            "Place the new or moved object centered at that red dot and completely replace the dot with the object. "
            f"The object and its shadow must fit within about {width_percent}% of the image width and {height_percent}% of the image height around that center. "
            "Keep the original background, lighting and textures. Do not include any dot, guide mark, box or colored overlay in the final photo.")


def append_masked_processing(graph, workflow, recipe, prefix, mask_name, reference_name=None):
    """A runnable crop-edit-blend graph using installed ComfyUI core nodes."""
    if not mask_name:
        raise ValueError("The selected-area workflow needs its saved mask.")
    x, y, width, height = recipe["masked_edit"]["crop"]
    graph["10"] = {"class_type": "ImageCrop", "inputs": {"image": ["5", 0], "width": width, "height": height, "x": x, "y": y}}
    graph["2"]["inputs"]["image"] = ["10", 0]
    graph["11"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
    graph["12"] = {"class_type": "ImageScale", "inputs": {"image": ["2", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}}
    graph["13"] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["5", 0], "source": ["12", 0], "mask": ["11", 1], "x": x, "y": y, "resize_source": False}}
    graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": prefix + "/final"}}
    improved = recipe["masked_edit"].get("version") == 2
    if improved:
        if not reference_name:
            raise ValueError("The selected-area workflow needs its aligned reference canvas.")
        left,top,cw,ch = recipe["masked_edit"]["canvas_content"]
        graph["14"] = {"class_type":"LoadImage","inputs":{"image":reference_name}}
        graph["15"] = {"class_type":"ImageCrop","inputs":{"image":["2",0],"x":left,"y":top,"width":cw,"height":ch}}
        graph["2"]["inputs"]["image"] = ["14",0]
        graph["12"]["inputs"]["image"] = ["15",0]
        del graph["10"]
    nodes = {n["id"]: n for n in workflow["nodes"]}
    # Remove the original edit-image and preview links before inserting a crop.
    detached = {nodes[2]["inputs"][1]["link"], nodes[3]["inputs"][0]["link"]}
    workflow["links"] = [link for link in workflow["links"] if link[0] not in detached]
    for existing in nodes.values():
        for output in existing.get("outputs", []):
            if output.get("links"):
                output["links"] = [link for link in output["links"] if link not in detached]
    next_link = max((link[0] for link in workflow["links"]), default=0) + 1

    def node(node_id, kind, widgets, inputs, position):
        result = {"id": node_id, "type": kind, "pos": position, "size": [320, 180], "flags": {}, "order": len(nodes), "mode": 0,
                  "inputs": [{"name": name, "type": value, "link": None} for name, value in inputs],
                  "outputs": [{"name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []}],
                  "properties": {"Node name for S&R": kind}, "widgets_values": widgets}
        workflow["nodes"].append(result); nodes[node_id] = result

    def connect(origin, slot, target, target_slot, kind="IMAGE"):
        nonlocal next_link
        workflow["links"].append([next_link, origin, slot, target, target_slot, kind])
        output = nodes[origin]["outputs"][slot]
        if output.get("links") is None:
            output["links"] = []
        output["links"].append(next_link)
        nodes[target]["inputs"][target_slot]["link"] = next_link
        next_link += 1

    if not improved:
        node(10, "ImageCrop", [width, height, x, y], [("image", "IMAGE")], [380, 700])
    else:
        reference_node=copy.deepcopy(nodes[5]);reference_node.update(id=14,pos=[380,700],order=len(nodes),widgets_values=[reference_name,"image"])
        reference_node["widgets_values_named"]={"image":reference_name,"upload":"image"}
        for output in reference_node["outputs"]:output["links"]=[]
        workflow["nodes"].append(reference_node);nodes[14]=reference_node
        node(15,"ImageCrop",[cw,ch,left,top],[("image","IMAGE")],[1000,350])
    mask_node = copy.deepcopy(nodes[5]); mask_node.update(id=11, pos=[950, 740], order=len(nodes), widgets_values=[mask_name, "image"])
    mask_node["widgets_values_named"] = {"image": mask_name, "upload": "image"}
    for output in mask_node["outputs"]:
        output["links"] = []
    workflow["nodes"].append(mask_node); nodes[11] = mask_node
    node(12, "ImageScale", ["lanczos", width, height, "disabled"], [("image", "IMAGE")], [1300, 350])
    node(13, "ImageCompositeMasked", [x, y, False], [("destination", "IMAGE"), ("source", "IMAGE"), ("mask", "MASK")], [1660, 350])
    node(9, "SaveImage", [prefix + "/final"], [("images", "IMAGE")], [2020, 350])
    if improved:
        connect(14,0,2,1);connect(2,0,15,0);connect(15,0,12,0)
    else:
        connect(5, 0, 10, 0); connect(10, 0, 2, 1); connect(2, 0, 12, 0)
    connect(5, 0, 13, 0); connect(12, 0, 13, 1); connect(11, 1, 13, 2, "MASK")
    connect(13, 0, 9, 0); connect(13, 0, 3, 0)
    nodes[3]["pos"] = [2380, 350]
    workflow["last_node_id"] = max(nodes); workflow["last_link_id"] = next_link - 1


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
        self.resource_lock = asyncio.Lock()
        self.native = NativeEngine()

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
                elif node in ("12", "13"):
                    self.store.update_job(job_id, status="running", phase="Blending your selected area", progress=0.95)
                elif node == "9":
                    self.store.update_job(job_id, phase="Saving your image", progress=0.98)
            elif event["type"] == "progress":
                enhancing = self.store.job(job_id)["phase"] == "Enhancing image detail"
                fraction = data["value"] / max(1, data["max"])
                processed = self.store.job(job_id)["recipe"].get("processing", {}).get("method", "none") != "none"
                self.store.update_job(job_id, status="running", phase="Enhancing image detail" if enhancing else "Creating your image",
                                      progress=min(0.95, .65 + .3 * fraction if enhancing else fraction * (.6 if processed else .95)),
                                      progress_detail=json.dumps({"stage":"enhancing" if enhancing else "sampling", "value":data["value"], "total":data["max"]}))

    async def finish(self, job, history):
        recipe = job["recipe"]
        status = history.get("status", {})
        if status.get("status_str") != "success":
            events = dict(status.get("messages", []))
            error = events.get("execution_error", {}).get("exception_message", "The image engine stopped before finishing.")
            self.store.update_job(job["id"], status="cancelled" if job["cancel_requested"] else "failed",
                                  phase="Stopped", error=error[:1500], finished=time.time())
            return
        improved_mask = recipe.get("masked_edit",{}).get("version") == 2
        output_node = "2" if improved_mask else "9" if recipe.get("processing", {}).get("method", "none") != "none" else "2"
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
            expected = (recipe["generation_width"],recipe["generation_height"]) if improved_mask else (recipe["width"], recipe["height"])
            if image.size != expected:
                raise RuntimeError("The image engine returned an unexpected image size.")
            if json.loads(image.info.get("studio", "{}")) != recipe:
                raise RuntimeError("The image is missing its saved generation settings.")
        if improved_mask:
            await asyncio.to_thread(blend_selected,temporary,self.store.directory,recipe)
        elif recipe.get("masked_edit"):
            await asyncio.to_thread(protect_unselected, temporary, self.store.directory, recipe)
        temporary.replace(path)
        await asyncio.to_thread(make_thumbnail, path, self.store.directory / "thumbs" / (job["id"] + ".webp"))
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO images (id,created,filename,recipe) VALUES (?,?,?,?)",
                                  (job["id"], time.time(), path.name, json.dumps(recipe)))
            self.store.db.execute("UPDATE jobs SET status='complete',phase='Complete',progress=1,progress_detail=NULL,finished=? WHERE id=?",
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
                mask_name = reference_name = guide_name = None
                if job["recipe"].get("masked_edit"):
                    content = await asyncio.to_thread(engine_mask, self.store.directory, job["recipe"])
                    form = aiohttp.FormData()
                    form.add_field("image", io.BytesIO(content), filename=job["id"] + "-mask.png", content_type="image/png")
                    form.add_field("subfolder", "web-studio")
                    form.add_field("overwrite", "true")
                    uploaded = await self.request("POST", "/upload/image", data=form)
                    mask_name = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                    if job["recipe"]["masked_edit"].get("version") == 2:
                        content = await asyncio.to_thread(engine_reference,self.store.directory,job["recipe"])
                        form = aiohttp.FormData()
                        form.add_field("image",io.BytesIO(content),filename=job["id"]+"-reference.png",content_type="image/png")
                        form.add_field("subfolder","web-studio");form.add_field("overwrite","true")
                        uploaded = await self.request("POST","/upload/image",data=form)
                        reference_name = "/".join(filter(None,[uploaded.get("subfolder"),uploaded["name"]]))
                        guide = (engine_placement_guide,"placement") if job["recipe"]["masked_edit"].get("placement_guidance") == "visual_reference_v1" else \
                                (engine_shape_guide,"shape") if job["recipe"]["masked_edit"].get("shape_guidance") == "outline_reference_v1" else None
                        if guide:
                            content = await asyncio.to_thread(guide[0],self.store.directory,job["recipe"])
                            form = aiohttp.FormData()
                            form.add_field("image",io.BytesIO(content),filename=job["id"]+f"-{guide[1]}.png",content_type="image/png")
                            form.add_field("subfolder","web-studio");form.add_field("overwrite","true")
                            uploaded = await self.request("POST","/upload/image",data=form)
                            guide_name = "/".join(filter(None,[uploaded.get("subfolder"),uploaded["name"]]))
                graph, workflow = build_graph(job["recipe"], self.project_root, self.templates, upload_name, mask_name,reference_name,guide_name)
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
            try:
                if self.native.base:
                    status = await self.native.request(wake=True)
                    if status.get("error"):
                        raise RuntimeError(status["error"])
                if not await self.available():
                    self.store.update_job(job["id"], phase="Starting your Mac’s image engine" if self.native.base else "Waiting for your Mac’s image engine")
                    await asyncio.sleep(3)
                    continue
                if self.resource_lock.locked():
                    self.store.update_job(job["id"], phase="Waiting for automatic selection")
                async with self.native.activity(), self.resource_lock:
                    job=self.store.job(job["id"])
                    if not job:
                        continue
                    if job["cancel_requested"] and not job["prompt_id"]:
                        self.store.update_job(job["id"],status="cancelled",phase="Stopped",finished=time.time())
                        continue
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

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
from .media import media_file, save_jpeg
from .native import NativeEngine
from .closeup import merge_close_up
from .masking import engine_mask, engine_reference, engine_placement_guide, engine_shape_guide, blend_selected, protect_unselected
from .identity import engine_donor, identity_hint, refinement_recipe, swap_hint

QUALITIES = {
    "fast": {"label": "Fast", "description": "Quick previews", "model": "flux2-klein-9b-4bit",
             "sizes": {"square": [512, 512], "portrait": [512, 640], "landscape": [768, 512]}},
    "balanced": {"label": "Balanced", "description": "Everyday detail", "model": "flux2-klein-9b-8bit",
                 "sizes": {"square": [768, 768], "portrait": [640, 800], "landscape": [960, 640]}},
    "high": {"label": "High", "description": "More pixels, more time", "model": "flux2-klein-9b-8bit",
             "sizes": {"square": [1024, 1024], "portrait": [832, 1040], "landscape": [1152, 768]}},
}


def build_graph(recipe, project_root, templates, upload_name=None, mask_name=None, reference_name=None, guide_name=None, donor_names=(), head_names=None):
    editing = recipe["mode"] == "edit"
    generation_width = recipe.get("generation_width", recipe["width"])
    generation_height = recipe.get("generation_height", recipe["height"])
    processing = recipe.get("processing", {"method": "none"})
    processed = processing["method"] != "none"
    model = recipe["model_source"]
    prefix = "web-studio/" + recipe["image_id"]
    if recipe.get("_identity_head_pass"):
        prefix += "/head"
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
        if recipe.get("identity_swap"):
            if len(donor_names) != len(recipe["identity_swap"]["donor_crops"]):
                raise ValueError("The identity swap needs its reference photo.")
            swap = recipe["identity_swap"]
            # The full-body pass studies the outfit; the separate head pass gets its own close-up.
            references = donor_names[:1] if swap.get("head_pass") else donor_names
            paths = json.dumps([str(Path(project_root)/"runtime/input"/name) for name in references])
            text = (swap_hint(swap["mode"]) if swap.get("mode") else identity_hint(swap["parts"])) + (" Additional notes: " + swap["notes"] if swap["notes"] else "")
            graph["2"]["inputs"].update(image_paths=paths, prompt=text)
            nodes[2]["widgets_values"][0] = text
            nodes[2]["widgets_values"][1] = paths
        elif guide_name:
            paths=json.dumps([str(Path(project_root)/"runtime/input"/guide_name)])
            hint=guide_hint(recipe["masked_edit"])
            graph["2"]["inputs"].update(image_paths=paths,prompt=recipe["prompt"]+hint)
            nodes[2]["widgets_values"][0]=recipe["prompt"]+hint
            nodes[2]["widgets_values"][1]=paths
    elif processed:
        append_processing(graph, workflow, recipe, prefix)
    if recipe.get("identity_swap", {}).get("head_pass"):
        if not head_names:
            raise ValueError("The whole identity swap needs its head detail selection.")
        detail_graph, detail_workflow = build_graph(refinement_recipe(recipe), project_root, templates, upload_name,
                                                   head_names["mask"], head_names["reference"], donor_names=donor_names[1:])
        # Both passes share a model and original pose, in one recoverable ComfyUI job.
        remap = {node_id: str(int(node_id) + 100) for node_id in detail_graph}
        for node_id, node in detail_graph.items():
            for key, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and value[0] in remap:
                    node["inputs"][key] = [remap[value[0]], value[1]]
            graph[remap[node_id]] = node
        offset = max(link[0] for link in workflow["links"]) + 1
        for node in detail_workflow["nodes"]:
            node["id"] += 100
            node["pos"][1] += 1200
            for item in node.get("inputs", []):
                if item.get("link") is not None:
                    item["link"] += offset
            for item in node.get("outputs", []):
                if item.get("links"):
                    item["links"] = [link + offset for link in item["links"]]
            workflow["nodes"].append(node)
        workflow["links"] += [[link[0] + offset, link[1] + 100, link[2], link[3] + 100, *link[4:]] for link in detail_workflow["links"]]
        workflow["last_node_id"] = max(node["id"] for node in workflow["nodes"])
        workflow["last_link_id"] = max(link[0] for link in workflow["links"])
        append_identity_head_context(graph, workflow, recipe, head_names["pose"])
    return graph, workflow


def head_pose_mask(directory, recipe):
    detail = refinement_recipe(recipe)
    selection = detail["masked_edit"]
    # Restore only the old head, not the surrounding shirt/background in the edit area.
    detail["masked_edit"] = dict(selection, mask_id=selection["core_mask_id"], sha256=selection["core_sha256"],
                                core_mask_id=None, blend_mode="manual", feather=0)
    return engine_mask(directory, detail)


def append_identity_head_context(graph, workflow, recipe, pose_name):
    """Restore the original head pose over the new body before the detail pass.

    New clothing must be visible behind long hair; otherwise erasing an old braid
    in pass two could paste the original shirt back over the replacement outfit.
    """
    selection = recipe["identity_swap"]["head_pass"]["masked_edit"]
    x, y, width, height = selection["crop"]
    left, top, cw, ch = selection["canvas_content"]
    nodes = {node["id"]: node for node in workflow["nodes"]}
    pose = copy.deepcopy(nodes[111])
    pose.update(id=125, pos=[100, 2050], widgets_values=[pose_name, "image"], widgets_values_named={"image": pose_name, "upload": "image"})
    for output in pose["outputs"]:
        output["links"] = []
    nodes[125] = pose; workflow["nodes"].append(pose)
    graph["125"] = {"class_type": "LoadImage", "inputs": {"image": pose_name}}

    def add(node_id, kind, values, specs, widgets):
        graph[str(node_id)] = {"class_type": kind, "inputs": values}
        node = {"id": node_id, "type": kind, "pos": [400 + (node_id - 120) * 340, 2050], "size": [320, 180],
                "flags": {}, "order": len(nodes), "mode": 0, "properties": {"Node name for S&R": kind},
                "widgets_values": widgets, "inputs": [{"name": name, "type": typ, "link": None} for name, typ in specs],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []}]}
        workflow["nodes"].append(node); nodes[node_id] = node
        for slot, (name, typ) in enumerate(specs):
            origin, origin_slot = values[name]
            origin = int(origin)
            workflow["last_link_id"] += 1
            link_id = workflow["last_link_id"]
            workflow["links"].append([link_id, origin, origin_slot, node_id, slot, typ])
            nodes[origin]["outputs"][origin_slot].setdefault("links", []).append(link_id)
            node["inputs"][slot]["link"] = link_id

    add(120, "ImageCrop", {"image": ["5", 0], "x": x, "y": y, "width": width, "height": height},
        [("image", "IMAGE")], [width, height, x, y])
    add(121, "ImageCompositeMasked", {"destination": ["13", 0], "source": ["120", 0], "mask": ["125", 1], "x": x, "y": y, "resize_source": False},
        [("destination", "IMAGE"), ("source", "IMAGE"), ("mask", "MASK")], [x, y, False])
    add(122, "ImageCrop", {"image": ["121", 0], "x": x, "y": y, "width": width, "height": height},
        [("image", "IMAGE")], [width, height, x, y])
    add(123, "ImageScale", {"image": ["122", 0], "width": cw, "height": ch, "upscale_method": "lanczos", "crop": "disabled"},
        [("image", "IMAGE")], ["lanczos", cw, ch, "disabled"])
    add(124, "ImageCompositeMasked", {"destination": ["114", 0], "source": ["123", 0], "x": left, "y": top, "resize_source": False},
        [("destination", "IMAGE"), ("source", "IMAGE")], [left, top, False])

    def rewire(target, slot, origin, name):
        link_id = nodes[target]["inputs"][slot]["link"]
        link = next(link for link in workflow["links"] if link[0] == link_id)
        nodes[link[1]]["outputs"][link[2]]["links"].remove(link_id)
        link[1], link[2] = origin, 0
        nodes[origin]["outputs"][0]["links"].append(link_id)
        graph[str(target)]["inputs"][name] = [str(origin), 0]

    rewire(102, 1, 124, "image")
    rewire(113, 0, 13, "destination")
    workflow["last_node_id"] = 125


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


def save_result(temporary, path, recipe):
    """The engine hands back a PNG; the library keeps a JPEG with the recipe inside."""
    with Image.open(temporary) as image:
        save_jpeg(image, path.with_suffix(".jpg.part"), recipe)
    path.with_suffix(".jpg.part").replace(path)
    temporary.unlink()


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
                elif node in ("2", "102"):
                    self.store.update_job(job_id, status="running", phase="Refining head identity" if node == "102" else "Creating your image", progress_detail=None)
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
                refining = str(data.get("node")) == "102" or self.store.job(job_id)["phase"] == "Refining head identity"
                fraction = data["value"] / max(1, data["max"])
                processed = self.store.job(job_id)["recipe"].get("processing", {}).get("method", "none") != "none"
                self.store.update_job(job_id, status="running", phase="Refining head identity" if refining else "Enhancing image detail" if enhancing else "Creating your image",
                                      progress=min(0.95, .65 + .3 * fraction if enhancing else fraction * (.6 if processed else .95)),
                                      progress_detail=json.dumps({"stage":"head detail" if refining else "enhancing" if enhancing else "sampling", "value":data["value"], "total":data["max"]}))

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
        path = self.store.directory / "images" / (job["id"] + ".jpg")
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
            if recipe.get("identity_swap", {}).get("head_pass"):
                head_output = next((item for item in history.get("outputs", {}).get("102", {}).get("images", []) if item.get("type") == "output"), None)
                if not head_output:
                    raise RuntimeError("The image engine returned no head detail image.")
                async with self.session.get(self.base + "/view", params=head_output) as response:
                    response.raise_for_status()
                    head_content = await response.read()
                head_path = path.with_suffix(".head.part")
                try:
                    head_path.write_bytes(head_content)
                    with Image.open(head_path) as image:
                        if json.loads(image.info.get("studio", "{}")) != recipe:
                            raise RuntimeError("The head detail image is missing its saved generation settings.")
                    await asyncio.to_thread(blend_selected, head_path, self.store.directory, refinement_recipe(recipe), temporary)
                    head_path.replace(temporary)
                finally:
                    head_path.unlink(missing_ok=True)
        elif recipe.get("masked_edit"):
            await asyncio.to_thread(protect_unselected, temporary, self.store.directory, recipe)
        elif recipe.get("close_up"):
            await asyncio.to_thread(merge_close_up, temporary, self.store.directory, recipe)
        await asyncio.to_thread(save_result, temporary, path, recipe)
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
                    source = media_file(self.store.directory, "uploads", job["recipe"]["source_id"])
                    with source.open("rb") as file:
                        form = aiohttp.FormData()
                        form.add_field("image", file, filename=source.name, content_type="image/jpeg" if source.suffix == ".jpg" else "image/png")
                        form.add_field("subfolder", "web-studio")
                        form.add_field("overwrite", "true")
                        uploaded = await self.request("POST", "/upload/image", data=form)
                    upload_name = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                else:
                    upload_name = None
                mask_name = reference_name = guide_name = None
                donor_names = []
                head_names = None
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
                        if job["recipe"].get("identity_swap"):
                            for index,content in enumerate(await asyncio.to_thread(engine_donor,self.store.directory,job["recipe"])):
                                form = aiohttp.FormData()
                                form.add_field("image",io.BytesIO(content),filename=job["id"]+f"-identity-{index+1}.png",content_type="image/png")
                                form.add_field("subfolder","web-studio");form.add_field("overwrite","true")
                                uploaded = await self.request("POST","/upload/image",data=form)
                                donor_names.append("/".join(filter(None,[uploaded.get("subfolder"),uploaded["name"]])))
                            if job["recipe"]["identity_swap"].get("head_pass"):
                                head_names = {}
                                for kind, prepare in (("mask", engine_mask), ("reference", engine_reference)):
                                    content = await asyncio.to_thread(prepare, self.store.directory, refinement_recipe(job["recipe"]))
                                    form = aiohttp.FormData()
                                    form.add_field("image", io.BytesIO(content), filename=job["id"]+f"-head-{kind}.png", content_type="image/png")
                                    form.add_field("subfolder", "web-studio"); form.add_field("overwrite", "true")
                                    uploaded = await self.request("POST", "/upload/image", data=form)
                                    head_names[kind] = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                                content = await asyncio.to_thread(head_pose_mask, self.store.directory, job["recipe"])
                                form = aiohttp.FormData()
                                form.add_field("image", io.BytesIO(content), filename=job["id"]+"-head-pose.png", content_type="image/png")
                                form.add_field("subfolder", "web-studio"); form.add_field("overwrite", "true")
                                uploaded = await self.request("POST", "/upload/image", data=form)
                                head_names["pose"] = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                graph, workflow = build_graph(job["recipe"], self.project_root, self.templates, upload_name, mask_name,reference_name,guide_name,donor_names,head_names)
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
                if self.resource_lock.locked():
                    self.store.update_job(job["id"], phase="Waiting for the current video or selection")
                async with self.resource_lock:
                    job=self.store.job(job["id"])
                    if not job:
                        continue
                    if job["cancel_requested"] and not job["prompt_id"]:
                        self.store.update_job(job["id"],status="cancelled",phase="Stopped",finished=time.time())
                        continue
                    if self.native.base:
                        status = await self.native.request(wake=True)
                        if status.get("error"):
                            raise RuntimeError(status["error"])
                        if status.get("state") == "video":
                            self.store.update_job(job["id"], phase="Waiting for the current video")
                            await asyncio.sleep(3)
                            continue
                    if not await self.available():
                        self.store.update_job(job["id"], phase="Starting your Mac’s image engine" if self.native.base else "Waiting for your Mac’s image engine")
                        await asyncio.sleep(3)
                        continue
                    async with self.native.activity():
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

"""Native ComfyUI Qwen editing graph and an equivalent reusable UI workflow."""


def graph_for(recipe, upload_name):
    r = recipe
    prefix = "web-studio/" + r["image_id"]
    if r.get("processing", {}).get("method", "none") != "none":
        prefix += "/base"
    graph = {
        "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {
            "unet_name": r["model_source"]["comfy_filename"],
            **r.get("gguf_settings", {"dequant_dtype": "float16", "patch_dtype": "float16", "patch_on_device": False})}},
        "10": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": r["encoder"]["comfy_filename"], "type": "qwen_image"}},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": r["vae"]["comfy_filename"]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": upload_name}},
        "13": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["10", 0],
               "prompt": r["prompt"], "vae": ["11", 0], "image1": ["5", 0],
               "reference_resolution": r.get("reference_resolution", 1024)}},
        "14": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["10", 0],
               "prompt": r["negative_prompt"], "vae": ["11", 0], "image1": ["5", 0],
               "reference_resolution": r.get("reference_resolution", 1024)}},
        "15": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
               "conditioning": ["13", 0], "reference_latents_method": r["reference_method"]}},
        "17": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
               "conditioning": ["14", 0], "reference_latents_method": r["reference_method"]}},
        "16": {"class_type": "EmptySD3LatentImage", "inputs": {
               "width": r.get("generation_width", r["width"]), "height": r.get("generation_height", r["height"]), "batch_size": 1}},
    }
    model = ["1", 0]
    for index, lora in enumerate(r["loras"], 30):
        graph[str(index)] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "lora_name": lora["comfy_filename"], "strength_model": lora["strength"]}}
        model = [str(index), 0]
    graph.update({
        "18": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": model, "shift": r["shift"]}},
        "19": {"class_type": "CFGNorm", "inputs": {"model": ["18", 0], "strength": 1.0}},
        "20": {"class_type": "KSampler", "inputs": {"model": ["19", 0], "seed": r["seed"],
               "steps": r["steps"], "cfg": r["cfg"], "sampler_name": r["sampler"], "scheduler": r["scheduler"],
               "positive": ["15", 0], "negative": ["17", 0], "latent_image": ["16", 0], "denoise": 1.0}},
        "24": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": ["11", 0]}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["24", 0], "filename_prefix": prefix}},
        "3": {"class_type": "PreviewImage", "inputs": {"images": ["24", 0]}},
    })
    return graph, ui_workflow(graph, r["preset_label"])


OUTPUTS = {"UnetLoaderGGUFAdvanced": ["MODEL"], "CLIPLoaderGGUF": ["CLIP"], "VAELoader": ["VAE"],
           "LoadImage": ["IMAGE", "MASK"], "TextEncodeQwenImageEditPlus": ["CONDITIONING"],
           "FluxKontextMultiReferenceLatentMethod": ["CONDITIONING"], "EmptySD3LatentImage": ["LATENT"],
           "LoraLoaderModelOnly": ["MODEL"], "ModelSamplingAuraFlow": ["MODEL"], "CFGNorm": ["MODEL"],
           "KSampler": ["LATENT"], "VAEDecode": ["IMAGE"], "SaveImage": [], "PreviewImage": []}


def ui_workflow(graph, title):
    nodes, links = {}, []
    for order, (key, entry) in enumerate(graph.items()):
        kind = entry["class_type"]
        widgets = []
        for name, value in entry["inputs"].items():
            if isinstance(value, list):
                continue
            widgets.append(value)
            if kind == "KSampler" and name == "seed":
                widgets.append("fixed")
        if kind == "LoadImage":
            widgets.append("image")
        nodes[key] = {"id": int(key), "type": kind, "pos": [(order % 4)*370, (order // 4)*350],
                      "size": [330, 260], "flags": {}, "order": order, "mode": 0,
                      "inputs": [], "outputs": [{"name": t, "type": t, "slot_index": i, "links": []}
                                                for i,t in enumerate(OUTPUTS[kind])],
                      "properties": {"Node name for S&R": kind}, "widgets_values": widgets}
    for key, entry in graph.items():
        for name, value in entry["inputs"].items():
            if not isinstance(value, list):
                continue
            origin, slot = value
            link = len(links) + 1
            kind = nodes[origin]["outputs"][slot]["type"]
            target_slot = len(nodes[key]["inputs"])
            links.append([link, int(origin), slot, int(key), target_slot, kind])
            nodes[origin]["outputs"][slot]["links"].append(link)
            nodes[key]["inputs"].append({"name": name, "type": kind, "link": link})
    return {"last_node_id": max(n["id"] for n in nodes.values()), "last_link_id": len(links),
            "nodes": list(nodes.values()), "links": links, "groups": [], "config": {},
            "extra": {"studio_preset": title}, "version": 0.4}

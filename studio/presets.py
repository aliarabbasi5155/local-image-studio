"""Model presets and self-contained, reproducible model choices."""
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QWEN = json.loads((ROOT / "config/qwen.json").read_text())
PRESETS = {
    "flux": {"label": "Flux2 Klein 9B", "family": "flux", "modes": ["create", "edit"],
             "description": "Everyday creation and editing · 4 steps"},
}
for key, label, base, adapter, lightning in (
    ("qwen-quality-snofs", "Qwen · Quality · SNOFS 1.3", "q4_k_m", "snofs", False),
    ("qwen-fast-snofs", "Qwen · Fast · SNOFS 1.3", "q4_0", "snofs", True),
    ("qwen-fast-mcnl", "Qwen · Fast · MCNL v1", "q4_0", "mcnl", True),
    ("qwen-quality-mcnl", "Qwen · Quality · MCNL v1", "q4_k_m", "mcnl", False),
):
    PRESETS[key] = {"label": label, "family": "qwen", "modes": ["edit"], "base": base,
                    "adapter": adapter, "lightning": lightning,
                    "description": "8 steps · Lightning" if lightning else "40 steps · higher precision",
                    "steps": 8 if lightning else 40, "cfg": 1.0 if lightning else 4.0}


def recipe_model(preset, quality, snofs, models, flux_adapter):
    choice = PRESETS[preset]
    if choice["family"] == "flux":
        key = quality["model"]
        return {"preset": preset, "preset_label": choice["label"], "family": "flux",
                "model": key, "model_source": models[key], "steps": 4, "guidance": 1.0,
                "snofs": snofs, "adapter": flux_adapter if snofs else None}
    assets = copy.deepcopy(QWEN["assets"])
    adapter = assets[choice["adapter"]]
    loras = ([dict(assets["lightning"], strength=1.0)] if choice["lightning"] else [])
    loras.append(dict(adapter, strength=1.0))
    return {"preset": preset, "preset_label": choice["label"], "family": "qwen",
            "base_model": QWEN["base_model"], "model": "qwen-edit-2511-" + choice["base"],
            "model_source": assets[choice["base"]], "encoder": assets["encoder"],
            "vision_encoder": assets["vision"], "vae": assets["vae"],
            "steps": choice["steps"], "guidance": 1.0, "cfg": choice["cfg"],
            "sampler": "euler", "scheduler": "simple", "shift": 3.1,
            "negative_prompt": "", "reference_method": "index_timestep_zero",
            "gguf_settings": {"dequant_dtype": "float16", "patch_dtype": "float16", "patch_on_device": True},
            "snofs": choice["adapter"] == "snofs", "adapter": adapter, "loras": loras,
            "gguf_node": QWEN["gguf_node"], "local_patches": QWEN["local_patches"],
            "runtime_options": QWEN["runtime_options"]}


def reference_resolution(sizing):
    """Match reference detail to the native canvas, before any output upscaling."""
    pixels = sizing["generation_width"] * sizing["generation_height"]
    return min(1024, max(128, round(math.sqrt(pixels) / 16) * 16))

"""CUDA equivalents of the Studio Klein nodes, installed only in the CUDA checkout.

Keep the workflow interface stable. Load one component at a time so the 9B
model works with 6 GB VRAM and 16 GB RAM using ComfyUI's layer offloading.
"""
import gc
import json
import logging
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
import nodes
import folder_paths
import comfy.model_management as memory
import comfy.utils
from comfy_api.latest import ComfyExtension, io, ui
from comfy_extras.nodes_custom_sampler import BasicGuider, KSamplerSelect, RandomNoise, SamplerCustomAdvanced
from comfy_extras.nodes_edit_model import ReferenceLatent
from comfy_extras.nodes_flux import EmptyFlux2LatentImage, Flux2Scheduler

ROOT = Path(__file__).resolve().parents[4]
CONFIG = json.loads((ROOT / "config/cuda.json").read_text())


def release():
    memory.unload_all_models()
    gc.collect()
    memory.soft_empty_cache()
    memory.throw_exception_if_processing_interrupted()


def encode_prompt(prompt):
    clip = nodes.NODE_CLASS_MAPPINGS["CLIPLoaderGGUF"]().load_clip(
        CONFIG["text_encoder"]["filename"], type="flux2")[0]
    return nodes.CLIPTextEncode().encode(clip, prompt)[0]


def encode_references(conditioning, references):
    vae = nodes.VAELoader().load_vae(Path(CONFIG["vae"]["filename"]).name)[0]
    for pixels in references:
        # Match the model's 16-pixel grid without silently reducing output size.
        h, w = pixels.shape[1:3]
        h, w = max(16, h // 16 * 16), max(16, w // 16 * 16)
        pixels = comfy.utils.common_upscale(pixels.movedim(-1, 1), w, h, "lanczos", "disabled").movedim(1, -1)
        latent = nodes.VAEEncodeTiled().encode(vae, pixels, tile_size=512, overlap=64)[0]
        conditioning = ReferenceLatent.execute(conditioning, latent)[0]
    return conditioning


def sample(pipeline, conditioning, width, height, steps, seed):
    model = nodes.NODE_CLASS_MAPPINGS["UnetLoaderGGUF"]().load_unet(
        CONFIG["models"][pipeline["quantize"]]["filename"], patch_on_device=True)[0]
    for line in pipeline["loras"].splitlines():
        if line.strip():
            name, separator, strength = line.strip().rpartition(":")
            if not separator:
                name, strength = line.strip(), "1.0"
            model = nodes.LoraLoaderModelOnly().load_lora_model_only(model, name, float(strength))[0]
    guider = BasicGuider.execute(model, conditioning)[0]
    noise = RandomNoise.execute(seed)[0]
    sampler = KSamplerSelect.execute("euler")[0]
    sigmas = Flux2Scheduler.execute(steps, width, height)[0]
    latent = EmptyFlux2LatentImage.execute(width, height)[0]
    return SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent)[0]


def decode(latent):
    vae = nodes.VAELoader().load_vae(Path(CONFIG["vae"]["filename"]).name)[0]
    return nodes.VAEDecodeTiled().decode(vae, latent, tile_size=512, overlap=64)[0]


class _KleinLoader:
    """Shared schema and execution, so each node class keeps its own output type."""

    editing = False

    @classmethod
    def define_schema(cls):
        return io.Schema(node_id=cls.__name__, category="Studio/CUDA", inputs=[
            io.Combo.Input("model_name", options=["flux2-klein-9b"]),
            io.Combo.Input("quantize", options=["8", "4"], default="8"),
            io.String.Input("custom_model_path", default="", optional=True),
            io.String.Input("loras", default="", multiline=True, optional=True),
        ], outputs=[io.Custom("FLUX2KLEIN_EDIT_PIPELINE" if cls.editing else "FLUX2KLEIN_PIPELINE").Output()])

    @classmethod
    def execute(cls, model_name, quantize, custom_model_path="", loras=""):
        if model_name != "flux2-klein-9b" or quantize not in CONFIG["models"]:
            raise ValueError("This CUDA installation supports Klein 9B Q4 and Q8.")
        return io.NodeOutput({"quantize": quantize, "loras": loras})


# Never derive one registered node from another: ComfyUI caches RETURN_TYPES on the
# class that builds its schema first, so a subclass would inherit the parent's type.
class Flux2KleinModelLoader(_KleinLoader, io.ComfyNode):
    pass


class Flux2KleinEditModelLoader(_KleinLoader, io.ComfyNode):
    editing = True


class _KleinGenerate:
    editing = False

    @classmethod
    def define_schema(cls):
        inputs = [io.Custom("FLUX2KLEIN_EDIT_PIPELINE" if cls.editing else "FLUX2KLEIN_PIPELINE").Input("pipeline"),
                  io.String.Input("prompt", multiline=True, default="")]
        if cls.editing:
            inputs.append(io.String.Input("image_paths", multiline=True, default=""))
        inputs += [io.Image.Input("image", optional=True),
                   io.Int.Input("height", default=1024, min=64, max=8192, step=16),
                   io.Int.Input("width", default=1024, min=64, max=8192, step=16),
                   io.Int.Input("num_inference_steps", default=4, min=1, max=50),
                   io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFF),
                   io.Float.Input("guidance", default=1.0, min=1.0, max=1.0)]
        if cls.editing:
            inputs.append(io.Boolean.Input("use_kv_cache", default=False, optional=True))
        else:
            inputs.append(io.Float.Input("image_strength", default=0.3, optional=True))
        inputs.append(io.String.Input("filename_prefix", default="studio-cuda/image"))
        return io.Schema(node_id=cls.__name__, category="Studio/CUDA", inputs=inputs,
                         hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
                         is_output_node=True, outputs=[io.Image.Output()])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    def execute(cls, pipeline, prompt, height, width, num_inference_steps, seed, guidance,
                filename_prefix, image=None, image_paths="", use_kv_cache=False, image_strength=0.3):
        if width % 16 or height % 16:
            raise ValueError("CUDA generation dimensions must be multiples of 16.")
        if guidance != 1.0 or use_kv_cache:
            raise ValueError("The distilled CUDA model uses guidance 1 and no KV cache.")
        if not cls.editing and image is not None:
            raise ValueError("Use the Edit workflow for image-conditioned generation on CUDA.")
        references = [image] if image is not None else []
        if image_paths.strip():
            paths = json.loads(image_paths) if image_paths.lstrip().startswith("[") else image_paths.splitlines()
            for name in paths:
                path = Path(name).resolve()
                if not path.is_relative_to(Path(folder_paths.get_input_directory()).resolve()):
                    raise ValueError("Reference images must be uploaded to ComfyUI's input directory.")
                with Image.open(path) as source:
                    pixels = np.asarray(ImageOps.exif_transpose(source).convert("RGB"), dtype=np.float32) / 255
                references.append(torch.from_numpy(pixels).unsqueeze(0))
        if cls.editing and not references:
            raise ValueError("Upload an image to edit.")
        try:
            release()
            logging.info("Studio CUDA: encoding prompt with Qwen3 8B Q8")
            conditioning = encode_prompt(prompt)
            release()
            if references:
                conditioning = encode_references(conditioning, references)
                release()
            logging.info("Studio CUDA: sampling Klein 9B Q%s, %sx%s, %s steps", pipeline["quantize"], width, height, num_inference_steps)
            latent = sample(pipeline, conditioning, width, height, num_inference_steps, seed)
            release()
            result = decode(latent)
            release()
            saved = ui.ImageSaveHelper.save_images(result, filename_prefix=filename_prefix,
                                                   folder_type=io.FolderType.output, cls=cls)
            return io.NodeOutput(result, ui=ui.SavedImages(saved))
        finally:
            memory.unload_all_models()
            gc.collect()
            memory.soft_empty_cache()


class Flux2KleinGenerate(_KleinGenerate, io.ComfyNode):
    pass


class Flux2KleinEdit(_KleinGenerate, io.ComfyNode):
    editing = True


class StudioCUDA(ComfyExtension):
    async def get_node_list(self):
        return [Flux2KleinModelLoader, Flux2KleinEditModelLoader, Flux2KleinGenerate, Flux2KleinEdit]


async def comfy_entrypoint():
    return StudioCUDA()

"""Plan the model canvas separately from the exact saved image dimensions."""
import math

OUTPUT_SIZES = {
    "preset": {"label": "Preset", "description": "Use the quality preset's size"},
    "original": {"label": "Match original", "description": "Keep the editing photo's exact size"},
    "2k": {"label": "2K", "description": "2048 pixels on the longest edge", "long_edge": 2048},
    "4k": {"label": "4K", "description": "4096 pixels on the longest edge", "long_edge": 4096},
}
MAX_PIXELS = 16_777_216
MAX_SIDE = 8192


def plan_size(quality, aspect, mode, source, output_size, qualities, upscaler):
    if output_size not in OUTPUT_SIZES:
        raise ValueError("Choose Preset, Match original, 2K or 4K output.")
    preset_width, preset_height = qualities[quality]["sizes"][aspect]
    if output_size == "original" and (mode != "edit" or not source):
        raise ValueError("Match original size is available when editing a photo.")
    if output_size == "preset":
        width, height = preset_width, preset_height
        generation_width, generation_height = width, height
    else:
        shape_width, shape_height = (source["width"], source["height"]) if mode == "edit" else (preset_width, preset_height)
        if output_size == "original":
            width, height = shape_width, shape_height
        else:
            scale = OUTPUT_SIZES[output_size]["long_edge"] / max(shape_width, shape_height)
            width, height = max(1, round(shape_width * scale)), max(1, round(shape_height * scale))
        if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
            raise ValueError("Match original supports photos up to 16 megapixels and 8192 pixels per side. Choose 2K or 4K for larger photos.")
        # Keep inference near the tested 1 MP budget, with enough real model
        # pixels for the 4x neural upscaler to cover the requested output.
        longest = max(width, height)
        desired = max(min(longest, max(preset_width, preset_height)), longest / upscaler["scale"])
        scale = min(1, desired / longest, math.sqrt(1_048_576 / (width * height)), 2048 / longest)
        generation_width = max(64, 16 * round(width * scale / 16))
        generation_height = max(64, 16 * round(height * scale / 16))
        if max(width / height, height / width) > 32:
            raise ValueError("For original-shape output, choose a photo with an aspect ratio between 1:32 and 32:1.")
    needs_upscale = width > generation_width or height > generation_height
    needs_resize = (width, height) != (generation_width, generation_height)
    return {"output_size": output_size, "width": width, "height": height,
            "generation_width": generation_width, "generation_height": generation_height,
            "processing": {"method": "ai_upscale" if needs_upscale else "resize" if needs_resize else "none",
                           "upscaler": upscaler if needs_upscale else None,
                           "resize_method": "lanczos" if needs_resize else None, "crop": "disabled"}}

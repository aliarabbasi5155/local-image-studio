"""Crop selected areas for FLUX editing and protect the original pixels."""
import hashlib
import io

from PIL import Image, ImageChops, ImageFilter, ImageOps, PngImagePlugin

from .sizing import MAX_PIXELS, MAX_SIDE, plan_size


def mask_size(width, height):
    scale = min(1, 2048 / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def normalize_mask(content, source, destination):
    size = source["width"], source["height"]
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.width * image.height > MAX_PIXELS:
            raise ValueError("Choose a PNG selection mask under 16 megapixels.")
        if image.size not in (size, mask_size(*size)):
            raise ValueError("This mask does not match the selected photo.")
        mask = image.convert("L").point(lambda value: 255 if value >= 128 else 0)
        if mask.size != size:
            mask = mask.resize(size, Image.Resampling.NEAREST)
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError("Paint an area to edit first.")
    mask.save(destination, "PNG")
    return {"width": size[0], "height": size[1], "bounds": list(bounds),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "coverage": round(mask.histogram()[255] / (size[0] * size[1]), 4)}


def masked_size(source, bounds, feather, quality, aspect, qualities, upscaler):
    if type(feather) is not int or not 0 <= feather <= 32:
        raise ValueError("Choose an edge softness between 0 and 32 pixels.")
    if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds):
        raise ValueError("Paint a valid selection first.")
    x0, y0, x1, y1 = bounds
    width, height = source["width"], source["height"]
    if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise ValueError("Selected-area editing supports photos up to 16 megapixels and 8192 pixels per side.")
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("This selection does not match the selected photo.")
    # Context is visible to the editor but cannot be pasted outside the mask.
    left, top = max(0, x0 - 64), max(0, y0 - 64)
    right, bottom = min(width, x1 + 64), min(height, y1 + 64)
    crop = [left, top, right - left, bottom - top]
    sizing = plan_size(quality, aspect, "edit", {"width": crop[2], "height": crop[3]},
                       "original", qualities, upscaler, "native")
    sizing.update(width=width, height=height, requested_width=width, requested_height=height,
                  output_size="original", resize_mode="masked",
                  processing={"method": "masked_patch", "upscaler": None,
                              "resize_method": "lanczos", "crop": crop})
    return sizing


def engine_mask(directory, recipe):
    """ComfyUI LoadImage's MASK output is the inverse of its PNG alpha."""
    selection = recipe["masked_edit"]
    with Image.open(directory / "masks" / (selection["mask_id"] + ".png")) as raw:
        mask = raw.convert("L")
    if hashlib.sha256((directory / "masks" / (selection["mask_id"] + ".png")).read_bytes()).hexdigest() != selection["sha256"]:
        raise ValueError("The saved selection mask has changed. Paint it again.")
    if selection["feather"]:
        mask = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(selection["feather"])))
    x, y, width, height = selection["crop"]
    mask = mask.crop((x, y, x + width, y + height))
    image = Image.new("RGBA", mask.size, "white")
    image.putalpha(ImageOps.invert(mask))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def protect_unselected(path, directory, recipe):
    """Avoid any float/PNG roundtrip changing pixels outside the selection."""
    with Image.open(path) as generated, Image.open(directory / "uploads" / (recipe["source_id"] + ".png")) as source, Image.open(directory / "masks" / (recipe["masked_edit"]["mask_id"] + ".png")) as raw:
        metadata = PngImagePlugin.PngInfo()
        for key in ("prompt", "workflow", "studio"):
            if key in generated.info:
                metadata.add_text(key, generated.info[key])
        original = source.convert("RGB")
        if generated.size != original.size or raw.size != original.size:
            raise ValueError("The masked result does not match the original photo dimensions.")
        result = Image.composite(generated.convert("RGB"), original, raw.convert("L"))
        result.save(path, "PNG", pnginfo=metadata)

"""Close-ups: a new, larger image of one area of a photo that stays consistent with it.

Upscaling alone looks artificial, and regenerating alone can drift. A close-up keeps every frequency the original
photo actually resolves and takes only finer detail from the model, so identity, shapes, colors and light come from
the photo and the model adds texture it never had. Where the model changed something, its detail is not used.
"""
import math

import numpy as np
from PIL import Image, ImageFilter

from .masking import match_colors
from .media import media_file

SIZES = ("standard", "2k", "4k")
# The merge scale, in multiples of the enlargement: at 1.0 the photo keeps every detail it resolves.
DETAIL = {"faithful": 1.0, "detailed": 1.6}
# Above this difference (0–255) between the model's and the photo's coarse image, the model changed content.
CHANGED = 28
CLOSE_UP_PROMPT = ("Make this a sharp, high-resolution close-up photograph of exactly the same subject. Keep the identity, "
                   "facial features, expression, pose, framing, proportions, colors and lighting exactly as they are. "
                   "Add only natural fine detail, such as skin texture, individual hair strands, fabric weave and surface grain. "
                   "Do not add, remove, move or restyle anything.")


def close_up_prompt(subject):
    return CLOSE_UP_PROMPT + (f" The subject is: {subject}." if subject else "")


def close_up_aspect(box):
    return "landscape" if box[2] > box[3] else "portrait" if box[3] > box[2] else "square"


def close_up_size(box, size, quality_sizes):
    """Output dimensions for a crop: about one megapixel generated directly, or 2K/4K on the long edge."""
    width, height = box[2], box[3]
    if size == "standard":
        # The quality preset for this shape caps the model's native canvas.
        long_edge = max(quality_sizes[close_up_aspect(box)])
        scale = min(long_edge / max(width, height), math.sqrt(1_048_576 / (width * height)), 2048 / max(width, height))
        # The model works on 16-pixel steps; rounding down keeps the generation canvas exactly these dimensions.
        return max(64, 16 * math.floor(width * scale / 16)), max(64, 16 * math.floor(height * scale / 16))
    scale = (2048 if size == "2k" else 4096) / max(width, height)
    return max(64, round(width * scale)), max(64, round(height * scale))


def crop_source(path, box, dimensions):
    with Image.open(path) as photo:
        return photo.convert("RGB").crop((box[0], box[1], box[0] + box[2], box[1] + box[3])).resize(dimensions, Image.Resampling.LANCZOS)


def _blur(array, sigma):
    return np.stack([np.asarray(Image.fromarray(array[..., channel]).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32)
                     for channel in range(3)], axis=-1)


def drift(source, generated):
    """The model's overall shift against the photo, to a fraction of a pixel, by phase correlation of coarse content."""
    reduce = min(1, 1024 / max(source.size))
    size = (max(16, round(source.width * reduce)), max(16, round(source.height * reduce)))
    def prepared(image):
        array = np.asarray(image.convert("L").resize(size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float64)
        return np.fft.fft2((array - array.mean()) * np.outer(np.hanning(size[1]), np.hanning(size[0])))
    spectrum = prepared(source) * np.conj(prepared(generated))
    surface = np.fft.ifft2(spectrum / (np.abs(spectrum) + 1e-9)).real
    y, x = np.unravel_index(np.argmax(surface), surface.shape)
    def refine(before, peak, after):
        curve = before - 2 * peak + after
        return .5 * (before - after) / curve if curve < 0 else 0
    dy = y + refine(surface[y - 1, x], surface[y, x], surface[(y + 1) % size[1], x])
    dx = x + refine(surface[y, x - 1], surface[y, x], surface[y, (x + 1) % size[0]])
    dy, dx = (dy + size[1] / 2) % size[1] - size[1] / 2, (dx + size[0] / 2) % size[0] - size[0] / 2
    return dx / reduce, dy / reduce, float(surface[y, x])


def merge_detail(source, generated, scale, detail="faithful"):
    """The photo below its own resolution, plus the model's finer detail wherever the model kept the content."""
    source = source.convert("RGB")
    generated = generated.convert("RGB")
    if generated.size != source.size:
        generated = generated.resize(source.size, Image.Resampling.LANCZOS)
    dx, dy, confidence = drift(source, generated)
    alignment = {"applied": False, "confidence": round(confidence, 3)}
    if confidence > .08 and abs(dx) < source.width * .05 and abs(dy) < source.height * .05 and max(abs(dx), abs(dy)) > .2:
        shifted = generated.transform(generated.size, Image.Transform.AFFINE, (1, 0, -dx, 0, 1, -dy), Image.Resampling.BICUBIC)
        # Uncovered edges come from the enlarged photo, so no black border can appear.
        valid = Image.new("L", generated.size, 255).transform(generated.size, Image.Transform.AFFINE, (1, 0, -dx, 0, 1, -dy), Image.Resampling.NEAREST)
        generated = Image.composite(shifted, source, valid)
        alignment.update(applied=True, offset=[round(dx, 2), round(dy, 2)])
    generated, colors = match_colors(source, generated, Image.new("L", source.size, 0))
    # The enlarged photo holds information up to its own Nyquist limit, 0.5 / scale cycles per output pixel.
    sigma = max(.8, scale * DETAIL[detail])
    original = np.asarray(source, dtype=np.uint8)
    candidate = np.asarray(generated, dtype=np.uint8)
    original_low, candidate_low = _blur(original, sigma), _blur(candidate, sigma)
    # Whether the model kept the content is judged on what the photo resolves, and a change anywhere nearby
    # withholds the model's detail, so a fine pattern cannot slip through between matching pixels.
    difference = np.abs(original_low - candidate_low).mean(axis=-1)
    trust = Image.fromarray((np.exp(-(difference / CHANGED) ** 2) * 255).astype(np.uint8))
    reach = 2 * math.ceil(sigma * 2) + 1
    trust = np.asarray(trust.filter(ImageFilter.MinFilter(min(reach, 31))).filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32)[..., None] / 255
    merged = original_low + (candidate.astype(np.float32) - candidate_low) * trust + (original.astype(np.float32) - original_low) * (1 - trust)
    result = Image.fromarray(np.clip(np.round(merged), 0, 255).astype(np.uint8))
    return result, {"sigma": round(sigma, 2), "alignment": alignment, "colors": colors, "model_detail_used": round(float(trust.mean()), 3)}


def merge_close_up(path, directory, recipe):
    """The engine's final step for a close-up, before the result is saved."""
    settings = recipe["close_up"]
    with Image.open(path) as generated, Image.open(media_file(directory, "uploads", recipe["source_id"])) as source:
        result, _ = merge_detail(source, generated, settings["scale"], settings["detail"])
    result.save(path, "PNG")

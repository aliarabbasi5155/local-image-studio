"""Identity swap: take a face, head, body, outfit or whole identity from a second photo.

SAM 3 finds the main person in each photo. The chosen parts become the area Flux repaints
in the first photo, using a crop of the second photo as its identity reference. Everything
outside that area, including parts that were not chosen, keeps its original pixels.
"""
import base64
import hashlib
import io

import numpy as np
from PIL import Image, ImageChops, ImageFilter

PARTS = ("face", "head", "body", "clothes")
MODES = {"head": ["head"], "whole": list(PARTS)}
CONCEPTS = ("hair", "person")
# Working canvas long side per quality (smallest, largest): a small head is enlarged so Flux can draw
# detail; a large area is reduced like other selected-area edits. In tests, keeping a full-length
# person near native size at Balanced cost ~50% more time for little visible gain.
WORKING_SIDE = {"fast": (640, 640), "balanced": (768, 768), "high": (1024, 1024)}
DONOR_SIDE = {"fast": 512, "balanced": 640, "high": 768}


def request_parts(payload):
    """Two public modes; old recipes can still be replayed through the parts API."""
    mode = payload.get("mode")
    if mode is None:
        return validate_parts(payload.get("parts"))
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("Choose Head identity or Whole identity.")
    parts = MODES[mode]
    if "parts" in payload and validate_parts(payload["parts"]) != parts:
        raise ValueError("The swap mode has changed. Preview the areas again.")
    return parts.copy()


def validate_parts(parts):
    if not isinstance(parts, list) or not parts or any(part not in PARTS for part in parts) or len(set(parts)) != len(parts):
        raise ValueError("Choose what to swap: face, head, body, clothes, or all of them for the whole identity.")
    # Canonical order keeps recipes, prompts and timing history comparable.
    return [part for part in PARTS if part in parts]


def _decode(rows, size):
    masks = []
    for row in rows:
        with Image.open(io.BytesIO(base64.b64decode(row["png"]))) as image:
            mask = np.asarray(image.convert("L").resize(size, Image.Resampling.BILINEAR)) >= 128
        if mask.any():
            masks.append(mask)
    return masks


def grow(mask, radius):
    """Square dilation by cumulative sums: fast for large radii on preview-sized masks."""
    radius = int(round(radius))
    if radius <= 0 or not mask.any():
        return mask.copy()
    result = mask
    for axis in (0, 1):
        counts = np.cumsum(np.pad(result.astype(np.int32), [(radius + 1, radius) if a == axis else (0, 0) for a in (0, 1)]), axis=axis)
        upper = counts.take(np.arange(2 * radius + 1, counts.shape[axis]), axis=axis)
        lower = counts.take(np.arange(0, counts.shape[axis] - 2 * radius - 1), axis=axis)
        result = (upper - lower) > 0
    return result


def _box(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def person_parts(result):
    """Face, hair and body of the most prominent person, on SAM's preview grid."""
    size = (result["width"], result["height"])
    people = _decode(result["concepts"].get("person", []), size)
    if not people:
        raise ValueError("No person was found in this photo. Choose a photo where the person is clearly visible.")
    person = max(people, key=lambda mask: mask.sum())
    others = np.zeros_like(person)
    for other in people:
        if other is not person and (other & person).sum() < .5 * other.sum():
            others |= other
    near = grow(person, max(2, 0.01 * max(size)))
    faces = [mask for mask in _decode(result["faces"], size) if (mask & near).sum() >= 0.5 * mask.sum()]
    face = max(faces, key=lambda mask: (mask & near).sum()) if faces else np.zeros_like(person)
    hair = np.zeros_like(person)
    for mask in _decode(result["concepts"].get("hair", []), size):
        if (mask & near).sum() >= 0.3 * mask.sum():
            hair |= mask & near
    # Face and hair overlap at the hairline; each swap decides which one owns those pixels.
    person = person | face | hair
    objects = np.zeros_like(person)
    for mask in _decode(result.get("candidates", []), size):
        # Object detections may overlap the person's mask (e.g. a glass in a hand).
        # Do not accept a spurious detection covering most of the person.
        if mask.sum() < .5 * person.sum() and (mask & near).any():
            objects |= mask
    return {"face": face, "hair": hair, "body": person & ~face & ~hair, "person": person,
            "objects": objects, "others": others, "size": size}


def swap_areas(result, parts, reference=None):
    """The repainted core, the full edit area (core plus extra room) and what stays protected."""
    regions = person_parts(result)
    face, hair, body, person = regions["face"], regions["hair"], regions["body"], regions["person"]
    if ("face" in parts or "head" in parts) and not face.any():
        raise ValueError("No face was found in this photo. Choose a photo where the face is visible.")
    x0, y0, x1, y1 = _box(person)
    person_height = y1 - y0
    head_box = _box(face | hair) if (face | hair).any() else [x0, y0, x1, y0 + person_height // 6]
    # Long hair must not turn a head edit's extra room into a torso-sized mask.
    head_height = head_box[3] - head_box[1]
    if face.any():
        head_height = min(head_height, 1.5 * (_box(face)[3] - _box(face)[1]))
    core, area = np.zeros_like(person), np.zeros_like(person)
    if set(parts) == set(PARTS):
        core = person
        area = grow(person, 0.05 * person_height)
    else:
        if "head" in parts:
            core |= face | hair
            # Include the ears and outer head, which a facial-feature mask can omit.
            if reference is not None:
                fx0, fy0, fx1, fy1 = _box(face)
                head = grow(face, .12 * (fx1 - fx0)) & person
                head[fy1 + max(1, round(.06 * (fy1 - fy0))):] = False
                core |= head
            # New hair can be longer or fuller than the old hairstyle.
            area |= grow(core, 0.22 * head_height)
        elif "face" in parts:
            # Like classic face swaps: an oval around the eyes, nose and mouth is replaced completely and
            # the rest of the face fades into the original. Flux tends to paint the reference's hairline,
            # bangs or temple hair at the edges; a gradual fade hides them and any skin tone difference,
            # where a hard edge showed a seam. The hair itself is never changed.
            fx0, fy0, fx1, fy1 = _box(face)
            rows, columns = np.ogrid[:face.shape[0], :face.shape[1]]
            oval = ((columns - (fx0 + fx1) / 2) / (0.36 * (fx1 - fx0))) ** 2 + ((rows - (fy0 + 0.58 * (fy1 - fy0))) / (0.42 * (fy1 - fy0))) ** 2 <= 1
            core |= face & oval & ~hair
            area |= grow(face & ~hair, 0.04 * (fy1 - fy0)) & ~hair
        if "body" in parts or "clothes" in parts:
            core |= body
            # A different build or garment shape needs room beyond the old outline, but not beside a
            # kept head, where Flux would paint the reference's hair.
            room = grow(body, 0.04 * person_height)
            if "head" not in parts:
                room &= ~grow(face | hair, 0.25 * head_height)
            area |= room | body
    if reference is not None and "head" in parts:
        # Reserve room for the donor's hair relative to their face. A short-haired
        # target's tight head mask otherwise clips a replacement bob or long hair.
        donor = person_parts(reference)
        if donor["face"].any() and donor["hair"].any():
            fx0, fy0, fx1, fy1 = _box(face)
            dx0, dy0, dx1, dy1 = _box(donor["face"])
            hx0, hy0, hx1, hy1 = _box(donor["hair"] | donor["face"])
            scale = min((fx1 - fx0) / (dx1 - dx0), (fy1 - fy0) / (dy1 - dy0))
            cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
            dcx, dcy = (dx0 + dx1) / 2, (dy0 + dy1) / 2
            room = np.zeros_like(person)
            left, right = max(0, int(cx + (hx0-dcx)*scale)), min(person.shape[1], int(np.ceil(cx + (hx1-dcx)*scale)))
            top, bottom = max(0, int(cy + (hy0-dcy)*scale)), min(person.shape[0], int(np.ceil(cy + (hy1-dcy)*scale)))
            room[top:bottom, left:right] = True
            area |= grow(room, .08 * head_height)
    protected = np.zeros_like(person)
    if "face" not in parts and "head" not in parts:
        protected |= face
    if "head" not in parts:
        protected |= hair
    protected &= ~core
    protected |= regions["objects"] | regions["others"]
    core &= ~protected
    area = (area | core) & ~protected
    return {"core": core, "area": area, "protected": protected, "size": regions["size"],
            "summary": {"face_found": bool(face.any()), "hair_found": bool(hair.any()),
                        "person_box": [x0, y0, x1, y1], "coverage": round(float(area.mean()), 4),
                        "objects_protected": bool(regions["objects"].any())}}


def blend_width(parts, bounds, automatic):
    """A face alone fades over about 15% of its size; other swaps use the usual adaptive edge."""
    if parts != ["face"]:
        return automatic
    return min(64, max(automatic, round(0.15 * max(bounds[2] - bounds[0], bounds[3] - bounds[1]))))


def close_up(parts):
    """A body guide carries no face, so a face or head swap with it adds the reference's face as image 3.

    Otherwise one reference works best: in tests an added close-up made Flux keep the old outfit.
    """
    return body_guide(parts) and ("face" in parts or "head" in parts)


def donor_crops(result, parts, width, height):
    """The parts of the reference photo Flux should study, in full-size pixels: the main crop first."""
    regions = person_parts(result)
    face, hair, person = regions["face"], regions["hair"], regions["person"]
    if ("face" in parts or "head" in parts) and not face.any():
        raise ValueError("No face was found in the reference photo. Choose a photo where the face is visible.")
    # A face crop stays tight: the reference's hair would otherwise be copied along with the face.
    head = (_box(face | hair), 0.18) if "head" in parts else ((_box(face), 0.08) if face.any() else None)
    boxes = [(_box(person), 0.06)] + ([head] if close_up(parts) else []) if "body" in parts or "clothes" in parts else [head]
    scale_x, scale_y = width / regions["size"][0], height / regions["size"][1]
    crops = []
    for (x0, y0, x1, y1), margin in boxes:
        pad = margin * max(x1 - x0, y1 - y0)
        left, top = max(0, int((x0 - pad) * scale_x)), max(0, int((y0 - pad) * scale_y))
        right, bottom = min(width, int(np.ceil((x1 + pad) * scale_x))), min(height, int(np.ceil((y1 + pad) * scale_y)))
        crops.append([left, top, right - left, bottom - top])
    return crops


def donor_body(result, photo):
    """The reference person's silhouette and skin tone, measured away from edges and hair."""
    regions = person_parts(result)
    skin = np.zeros_like(regions["person"])
    for mask in _decode(result.get("skin", []), regions["size"]):
        skin |= mask & regions["person"]
    sample = (skin | regions["face"]) & ~regions["hair"]
    sample &= ~grow(~sample, max(1, 0.004 * max(regions["size"])))
    if sample.sum() < 40:
        raise ValueError("No visible skin was found in the reference photo. Choose a photo where the face or arms are visible.")
    with Image.open(photo) as image:
        pixels = np.asarray(image.convert("RGB").resize(regions["size"], Image.Resampling.BILINEAR))
    return regions["person"], [int(round(value)) for value in np.median(pixels[sample], axis=0)]


def body_guide_image(directory, guide, crop):
    """Silhouette in the reference's skin tone on pale gray: build and color without any clothing."""
    x, y, width, height = crop
    path = directory / "masks" / (guide["mask_id"] + ".png")
    if hashlib.sha256(path.read_bytes()).hexdigest() != guide["sha256"]:
        raise ValueError("The saved reference silhouette has changed. Find the swap areas again.")
    with Image.open(path) as raw:
        silhouette = raw.convert("L").crop((x, y, x + width, y + height))
    size = max(3, 2 * round(max(width, height) / 400) + 1)
    outline = ImageChops.subtract(silhouette.filter(ImageFilter.MaxFilter(size)), silhouette.filter(ImageFilter.MinFilter(size)))
    image = Image.new("RGB", silhouette.size, (232, 232, 232))
    image.paste(tuple(guide["skin"]), mask=silhouette)
    image.paste(tuple(round(value * .55) for value in guide["skin"]), mask=outline)
    return image


def mask_png(mask, size):
    """A full-size binary PNG from a preview-grid mask."""
    image = Image.fromarray(mask.astype(np.uint8) * 255).resize(size, Image.Resampling.BILINEAR).point(lambda v: 255 if v >= 128 else 0)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def clothing_area(result, allowed):
    """Remove source wardrobe cues without erasing the head, visible skin or objects."""
    regions = person_parts(result)
    skin = regions["face"].copy()
    for mask in _decode(result.get("skin", []), regions["size"]):
        skin |= mask
    return regions["body"] & allowed & ~grow(skin, 2)


def neutralize_clothing(image, directory, recipe):
    """Suppress lapels, zippers and fabric that the model otherwise retains from the old outfit."""
    appearance = recipe.get("identity_swap", {}).get("appearance_mask")
    if not appearance or recipe.get("_identity_head_pass"):
        return image
    path = directory / "masks" / (appearance["mask_id"] + ".png")
    if hashlib.sha256(path.read_bytes()).hexdigest() != appearance["sha256"]:
        raise ValueError("The saved clothing guide has changed. Preview the swap again.")
    with Image.open(path) as raw:
        mask = raw.convert("L")
    gray = image.convert("L").filter(ImageFilter.GaussianBlur(max(image.size) * .05))
    # Broad shading keeps pose and volume; low contrast removes garment construction cues.
    gray = gray.point(lambda value: round(160 + (value - 128) * .15)).convert("RGB")
    return Image.composite(gray, image, mask)


def engine_donor(directory, recipe):
    """The reference crops, small enough to keep generation time modest."""
    swap = recipe["identity_swap"]
    limit = DONOR_SIDE[recipe["quality"]]
    references = []
    with Image.open(directory / "uploads" / (swap["donor_id"] + ".png")) as image:
        photo = image.convert("RGB")
    for index, (x, y, width, height) in enumerate(swap["donor_crops"]):
        crop = body_guide_image(directory, swap["body_guide"], [x, y, width, height]) if index == 0 and swap.get("body_guide") else \
            photo.crop((x, y, x + width, y + height))
        # A distant face may be only ~80 pixels: enlarged, Flux reads far more of its identity.
        scale = min(4, limit / max(crop.size))
        crop = crop.resize((max(16, 16 * round(crop.width * scale / 16)), max(16, 16 * round(crop.height * scale / 16))), Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        crop.save(stream, "PNG")
        references.append(stream.getvalue())
    return references


def refinement_recipe(recipe):
    """An independent head pass studies the ORIGINAL pose, never a generated face."""
    swap = recipe["identity_swap"]
    detail = swap["head_pass"]
    return dict(recipe, **detail["sizing"], masked_edit=detail["masked_edit"], _identity_head_pass=True,
                identity_swap=dict(swap, mode="head", parts=["head"], donor_crops=[swap["donor_crops"][1]], head_pass=None))


def swap_hint(mode):
    """Keep reference roles short and explicit; appearance comes only from the donor."""
    intro = ("Image 1 is the photo to edit and controls the composition. Image 2 is the identity reference. ")
    take = ("Replace only the person's entire head in image 1 with the head of the person in image 2, including their "
            "recognizable face, facial proportions, ears, skin appearance and hairstyle. Keep the body and clothing from image 1. ") if mode == "head" else (
            "Completely replace the person in image 1 with the person from image 2: their recognizable face, hairstyle, "
            "skin appearance, body build and exact outfit. Replace every original garment with the clothing from image 2, "
            "including the outermost layer. Wear only the reference outfit; do not retain or layer the original clothes over it. "
            "Fit the replacement person into the original person's position. ")
    return (intro + take +
            "Match the reference person's actual features and grooming exactly. Do not invent features or blend the two identities. "
            "Use image 1 for head rotation, tilt, gaze direction, expression, body pose and camera perspective. "
            "Adapt the reference identity to that angle; do not turn the head toward the reference camera. "
            "Keep the original arm positions, hand gestures, grip and contact with existing objects. "
            "Preserve their positions and which surfaces are in front of or behind each other. "
            "Keep all scene objects and other people from image 1. Do not import objects or background from image 2. "
            "Match image 1's lighting, shadows, framing and scale. Produce one natural photograph.")


def body_guide(parts):
    """A body swap that keeps the outfit shows Flux a silhouette: a reference photo's clothing is always copied."""
    return "body" in parts and "clothes" not in parts


def identity_hint(parts):
    """Instructions for the reference images; the user's own notes follow unchanged."""
    guide, closeup = body_guide(parts), close_up(parts)
    face_image = "image 3" if guide else "image 2"
    if guide:
        intro = ("Image 1 is the photo to edit. Image 2 is a body guide made from another person's photo, not a photo itself: its silhouette "
                 "shows the build, body shape and proportions to give the person in image 1, and its fill color is that person's skin tone. "
                 "Never copy its flat color, outline, pose or gray background into the photo.")
        intro += f" Image 3 shows the same person's {'head' if 'head' in parts else 'face'}." if closeup else ""
    else:
        intro = "Image 1 is the photo to edit. Image 2 shows a different person and is only a reference for their identity."
    if set(parts) == set(PARTS):
        take = ("Replace the person in image 1 with the person from image 2: the same face, facial features, skin tone, apparent age "
                "and gender, any beard or mustache, hairstyle, hair color, body build and the exact same outfit.")
        keep = []
    else:
        # "Facial hair" alone made Flux add a beard to references without one.
        details = {"face": "the face (the same facial features, eyes, eyebrows, nose, mouth, jawline, skin tone, apparent age and gender, "
                           f"with a beard or mustache only if {face_image} has one)",
                   "head": "the whole head (the same face, facial features, skin tone, apparent age and gender, hairstyle, hair color and "
                           f"hair length, with a beard or mustache only if {face_image} has one)",
                   "body": "the body (the same build, body shape, proportions and skin tone)",
                   "clothes": "the outfit (the same garments, colors, fabrics, patterns, fit, accessories and footwear)"}
        chosen = [details[part] for part in parts if not (part == "face" and "head" in parts) and not (part == "body" and guide)]
        take = "Give the person in image 1 " + (", ".join(chosen[:-1]) + " and " + chosen[-1] if len(chosen) > 1 else chosen[0]) + \
               f" of the person in {face_image}. " if chosen else ""
        if guide:
            take += ("Reshape the person in image 1 to the build, body shape, proportions and skin tone shown by image 2. They keep wearing "
                     "exactly the clothes from image 1, with the same garments, colors, fabrics and patterns, refitted to the new build; "
                     "visible skin such as the neck, arms, hands and legs takes the new skin tone.")
        take = take.strip()
        keep = []
        if "face" not in parts and "head" not in parts:
            keep.append("the face and hairstyle")
        elif "head" not in parts:
            keep.append("the hairstyle and hair color")
        if "body" not in parts:
            keep.append("the body shape and proportions")
            if "clothes" not in parts:
                keep.append("the outfit")
    keep_text = " Keep from image 1: " + "; ".join(keep) + "." if keep else ""
    # Inside a crop Flux tends to recolor the old hairstyle instead of replacing its shape.
    hair_text = (" Remove the original hairstyle of image 1 completely, including any braid, ponytail, bun or strands over the "
                 f"shoulders, and recreate the hairstyle from {face_image} exactly, with the same cut, length, volume and parting.") if "head" in parts else \
                (f" Change only the face. Do not copy any hair from {face_image}: keep the exact hairline, hair color, hairstyle, ears "
                 "and head size of image 1.") if "face" in parts else ""
    return (intro + " " + take + hair_text + keep_text +
            " Keep the exact pose, head angle, expression, position and size in the frame, background, lighting, shadows and camera angle of image 1."
            " The result must look like one natural photograph without seams, borders or a second person.")

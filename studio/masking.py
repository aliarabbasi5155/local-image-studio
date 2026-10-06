"""Crop selected areas for FLUX editing and protect the original pixels."""
import hashlib
import io
import json
import math

import numpy as np

from PIL import Image, ImageChops, ImageFilter, ImageOps, PngImagePlugin

from .sizing import MAX_PIXELS, MAX_SIDE


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


def masked_size(source, bounds, feather, quality, aspect, qualities, upscaler, detail="balanced", context="balanced"):
    if type(feather) is not int or not 0 <= feather <= 32:
        raise ValueError("Choose an edge softness between 0 and 32 pixels.")
    if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds):
        raise ValueError("Paint a valid selection first.")
    if detail not in ("quick", "balanced", "maximum") or context not in ("tight", "balanced", "wide"):
        raise ValueError("Choose a valid crop resolution and surrounding context.")
    x0, y0, x1, y1 = bounds
    width, height = source["width"], source["height"]
    if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise ValueError("Selected-area editing supports photos up to 16 megapixels and 8192 pixels per side.")
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("This selection does not match the selected photo.")
    # Context is visible to the editor but cannot be pasted outside the mask.
    extent = max(x1-x0,y1-y0)
    padding = {"tight":64,"balanced":min(256,max(96,round(extent*.15))),
               "wide":min(512,max(128,round(extent*.3)))}[context]
    left, top = max(0, x0 - padding), max(0, y0 - padding)
    right, bottom = min(width, x1 + padding), min(height, y1 + padding)
    crop = [left, top, right - left, bottom - top]
    limit = min(max(qualities[quality]["sizes"][aspect]), {"quick":512,"balanced":768,"maximum":2048}[detail])
    scale = min(1,limit/max(crop[2:]),math.sqrt(1_048_576/(crop[2]*crop[3])))
    while True:
        content_width, content_height = (max(1,round(value*scale)) for value in crop[2:])
        canvas_width, canvas_height = (max(64,16*math.ceil(value/16)) for value in (content_width,content_height))
        if canvas_width*canvas_height<=1_048_576:
            break
        scale *= .99
    content = [(canvas_width-content_width)//2,(canvas_height-content_height)//2,content_width,content_height]
    return {"width":width,"height":height,"requested_width":width,"requested_height":height,
            "generation_width":canvas_width,"generation_height":canvas_height,
            "output_size":"original","resize_mode":"masked","mask_detail":detail,"mask_context":context,
            "processing":{"method":"masked_patch_v2","upscaler":None,"resize_method":"lanczos",
                          "crop":crop,"canvas_content":content,"context_padding":padding}}


def engine_reference(directory, recipe):
    """The reference and output have exactly the same field of view and 16px grid."""
    selection = recipe["masked_edit"]
    x,y,width,height = selection["crop"]
    left,top,cw,ch = selection["canvas_content"]
    with Image.open(directory / "uploads" / (recipe["source_id"] + ".png")) as original:
        content = original.convert("RGB").crop((x,y,x+width,y+height)).resize((cw,ch),Image.Resampling.LANCZOS)
    # Replicate border pixels, rather than introducing black framing to the model.
    pixels = np.pad(np.asarray(content), ((top,recipe["generation_height"]-ch-top),
                    (left,recipe["generation_width"]-cw-left),(0,0)), mode="edge")
    stream = io.BytesIO(); Image.fromarray(pixels).save(stream,"PNG")
    return stream.getvalue()


def feather_mask(mask, radius):
    """Fade entirely inside the selection; a Gaussian alone starts at ~50% opacity."""
    if not radius:
        return mask
    inside = np.asarray(mask)>0
    distance = inside.astype(np.uint8)
    # Only distances up to the requested feather width are needed. Replicated
    # image borders do not introduce a false seam at the edge of the photograph.
    for _ in range(radius):
        padded = np.pad(inside,1,mode="edge")
        eroded = padded[:-2,:-2].copy()
        for dy,dx in ((0,1),(0,2),(1,0),(1,1),(1,2),(2,0),(2,1),(2,2)):
            eroded &= padded[dy:dy+inside.shape[0],dx:dx+inside.shape[1]]
        distance += eroded
        inside = eroded
        if not inside.any():
            break
    weights = .5-.5*np.cos(np.minimum(distance.astype(np.float32)/(radius+1),1)*np.pi)
    return Image.fromarray(np.round(weights*255).astype(np.uint8))


def _context_samples(original, patch, mask):
    """Compare only unpainted surroundings, never the object requested to change."""
    size = original.size
    scale = min(1,192/max(size))
    size = tuple(max(1,round(value*scale)) for value in size)
    original = original.resize(size,Image.Resampling.LANCZOS)
    patch = patch.resize(size,Image.Resampling.LANCZOS)
    selection = mask.resize(size,Image.Resampling.BOX).filter(ImageFilter.MaxFilter(5))
    context = np.asarray(selection)==0
    context[:3,:]=context[-3:,:]=False;context[:,:3]=context[:,-3:]=False
    return original,patch,context


def align_patch(original, patch, mask):
    """Correct small rigid drift only when unchanged context provides strong evidence."""
    target, candidate, context = _context_samples(original,patch,mask)
    if context.sum()<128 or context.mean()<.05:
        return patch,{"applied":False,"reason":"insufficient context"}
    def edges(image):
        array = np.asarray(image.convert("L").filter(ImageFilter.GaussianBlur(.7)),dtype=np.float32)
        dy,dx = np.gradient(array)
        return np.hypot(dx,dy)
    target_edges, patch_edges = edges(target),edges(candidate)
    contrast = np.quantile(target_edges[context],.5)
    context &= target_edges>=max(2,contrast)
    if context.sum()<64:
        return patch,{"applied":False,"reason":"no stable edges"}
    target_values = target_edges[context]
    def score(values):
        vector = values[context]
        return float(np.dot(target_values,vector)/(np.linalg.norm(target_values)*np.linalg.norm(vector)+1e-6))
    baseline = score(patch_edges)
    best = (baseline,(1,0,0))
    field = Image.fromarray(patch_edges)
    cx,cy = (value/2 for value in field.size)
    span = max(1,round(max(field.size)*.02))
    for scale in (.98,1,1.02):
        for dy in range(-span,span+1):
            for dx in range(-span,span+1):
                transformed = field.transform(field.size,Image.Transform.AFFINE,
                    (scale,0,dx+(1-scale)*cx,0,scale,dy+(1-scale)*cy),Image.Resampling.BILINEAR)
                current = score(np.asarray(transformed))
                if current>best[0]:best=(current,(scale,dx,dy))
    if best[0]<.65 or best[0]-baseline<.07 or best[0]<baseline*1.12:
        return patch,{"applied":False,"reason":"no reliable improvement","score":round(baseline,3)}
    scale,dx,dy = best[1]
    dx *= patch.width/field.width;dy *= patch.height/field.height
    transform = (scale,0,dx+(1-scale)*patch.width/2,0,scale,dy+(1-scale)*patch.height/2)
    aligned = patch.transform(patch.size,Image.Transform.AFFINE,transform,Image.Resampling.BICUBIC)
    valid = Image.new("L",patch.size,255).transform(patch.size,Image.Transform.AFFINE,transform,Image.Resampling.NEAREST)
    aligned = Image.composite(aligned,original,valid)
    return aligned,{"applied":True,"scale":scale,"offset":[round(dx,2),round(dy,2)],"score":round(best[0],3)}


def match_colors(original, patch, mask):
    target,candidate,context = _context_samples(original,patch,mask)
    if context.sum()<128 or context.mean()<.05:
        return patch,{"applied":False,"reason":"insufficient context"}
    target = np.asarray(target,dtype=np.float32)[context]
    candidate = np.asarray(candidate,dtype=np.float32)[context]
    # Robustly exclude changed objects/shadows in the supposed unchanged context.
    residual = np.mean(np.abs((target-candidate)-np.median(target-candidate,axis=0)),axis=1)
    stable = residual<=np.quantile(residual,.7)
    target,candidate = target[stable],candidate[stable]
    center_t,center_c = np.mean(target,axis=0),np.mean(candidate,axis=0)
    variance = np.mean((candidate-center_c)**2,axis=0)
    gain = np.clip(np.mean((target-center_t)*(candidate-center_c),axis=0)/np.maximum(variance,1),.85,1.18)
    gain = np.where(variance<16,1,gain)
    offset = np.clip(np.median(target-candidate*gain,axis=0),-40,40)
    before = np.mean(np.abs(target-candidate))
    after = np.mean(np.abs(target-(candidate*gain+offset)))
    if before<1 or after>before*.85:
        return patch,{"applied":False,"reason":"no reliable improvement"}
    table = [round(np.clip(value*gain[channel]+offset[channel],0,255)) for channel in range(3) for value in range(256)]
    return patch.point(table),{"applied":True,"gain":gain.round(3).tolist(),"offset":offset.round(2).tolist()}


def blend_selected(path, directory, recipe):
    """Compose the raw edited patch once, with corrected geometry, color and edges."""
    selection = recipe["masked_edit"]
    x,y,width,height = selection["crop"]
    left,top,cw,ch = selection["canvas_content"]
    with Image.open(path) as generated, Image.open(directory/"uploads"/(recipe["source_id"]+".png")) as source, Image.open(directory/"masks"/(selection["mask_id"]+".png")) as raw:
        if generated.size!=(recipe["generation_width"],recipe["generation_height"]):
            raise ValueError("The edited patch has an unexpected canvas size.")
        original=source.convert("RGB")
        if raw.size!=original.size:
            raise ValueError("The saved mask does not match the original.")
        patch=generated.convert("RGB").crop((left,top,left+cw,top+ch)).resize((width,height),Image.Resampling.LANCZOS)
        mask=raw.convert("L").crop((x,y,x+width,y+height))
        reference=original.crop((x,y,x+width,y+height))
        diagnostics={}
        if selection.get("align",True):patch,diagnostics["alignment"]=align_patch(reference,patch,mask)
        if selection.get("color_match",True):patch,diagnostics["color_match"]=match_colors(reference,patch,mask)
        original.paste(patch,(x,y),feather_mask(mask,selection["feather"]))
        metadata=PngImagePlugin.PngInfo()
        for key in ("prompt","workflow","studio"):
            if key in generated.info:metadata.add_text(key,generated.info[key])
        metadata.add_text("studio_mask_diagnostics",json.dumps(diagnostics))
        original.save(path,"PNG",pnginfo=metadata)
    return diagnostics


def engine_mask(directory, recipe):
    """ComfyUI LoadImage's MASK output is the inverse of its PNG alpha."""
    selection = recipe["masked_edit"]
    with Image.open(directory / "masks" / (selection["mask_id"] + ".png")) as raw:
        mask = raw.convert("L")
    if hashlib.sha256((directory / "masks" / (selection["mask_id"] + ".png")).read_bytes()).hexdigest() != selection["sha256"]:
        raise ValueError("The saved selection mask has changed. Paint it again.")
    if selection.get("version") == 2:
        mask = feather_mask(mask,selection["feather"])
    elif selection["feather"]:
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

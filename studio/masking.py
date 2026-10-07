"""Crop selected areas for FLUX editing and protect the original pixels."""
import hashlib
import io
import json
import math

import numpy as np

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, PngImagePlugin

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


def engine_placement_guide(directory, recipe):
    """A small destination marker avoids teaching Flux to copy a colored mask."""
    selection=recipe["masked_edit"]
    placement=selection["selection"]["placement"]
    x,y,width,height=selection["crop"]
    left,top,cw,ch=selection["canvas_content"]
    with Image.open(io.BytesIO(engine_reference(directory,recipe))) as reference:
        guide=reference.convert("RGB")
    box=(left+(placement[0]-x)*cw/width,top+(placement[1]-y)*ch/height,
         left+(placement[2]-x)*cw/width,top+(placement[3]-y)*ch/height)
    cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
    radius=max(3,min(8,(box[2]-box[0])/12,(box[3]-box[1])/12))
    ImageDraw.Draw(guide).ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill=(255,20,40))
    output=io.BytesIO();guide.save(output,"PNG");return output.getvalue()


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
    # Distances are small integers; a lookup avoids several full-size float
    # arrays while retaining exactly the same cosine transition at 4K.
    weights = .5-.5*np.cos(np.minimum(np.arange(256,dtype=np.float32)/(radius+1),1)*np.pi)
    return Image.fromarray(np.round(weights*255).astype(np.uint8)[distance])


def selection_alpha(directory, selection):
    """Blend weights for the edited crop; a detected object core is always fully replaced.

    Fading the object's own boundary lets the old garment show through wherever the
    selection meets a protected face or skin. Only the extra room around it fades.
    """
    x,y,width,height = selection["crop"]
    box = (x,y,x+width,y+height)
    # The crop includes context beyond the selection, and feathering replicates its
    # border, so cropping first gives the same weights for far less work on 4K photos.
    with Image.open(directory / "masks" / (selection["mask_id"] + ".png")) as raw:
        size = raw.size
        mask = raw.convert("L").crop(box)
    feather = selection.get("blend_width",selection["feather"]) if selection.get("blend_mode")=="adaptive" else selection["feather"]
    alpha = feather_mask(mask,feather)
    if not selection.get("core_mask_id"):
        return alpha
    path = directory / "masks" / (selection["core_mask_id"] + ".png")
    if hashlib.sha256(path.read_bytes()).hexdigest() != selection["core_sha256"]:
        raise ValueError("The saved object outline has changed. Select the area again.")
    with Image.open(path) as raw:
        if raw.size != size:
            raise ValueError("The saved object outline does not match the original.")
        core = raw.convert("L").crop(box)
    # The core never extends into protected or unselected pixels. A few pixels of
    # fade where it meets them avoid a hard seam inside skin.
    core = ImageChops.multiply(core,feather_mask(mask,selection.get("opaque_edge",0)))
    return ImageChops.lighter(alpha,core)


def engine_shape_guide(directory, recipe):
    """The original object's silhouette on the reference canvas, as a shape-only guide."""
    selection = recipe["masked_edit"]
    x,y,width,height = selection["crop"]
    left,top,cw,ch = selection["canvas_content"]
    name = selection.get("core_mask_id") or selection["mask_id"]
    with Image.open(directory / "masks" / (name + ".png")) as raw:
        shape = raw.convert("L").crop((x,y,x+width,y+height)).resize((cw,ch),Image.Resampling.BILINEAR)
    canvas = Image.new("L",(recipe["generation_width"],recipe["generation_height"]),0)
    canvas.paste(shape,(left,top))
    outline = ImageChops.subtract(canvas.filter(ImageFilter.MaxFilter(5)),canvas.filter(ImageFilter.MinFilter(5)))
    with Image.open(io.BytesIO(engine_reference(directory,recipe))) as reference:
        guide = ImageOps.grayscale(reference).point(lambda value: 150+value*105//255).convert("RGB")
    # Pale photo context with a solid dark silhouette and outline: geometry, not colors to copy.
    guide.paste((70,70,70),mask=canvas.point(lambda value: 255 if value >= 128 else 0))
    guide.paste((0,0,0),mask=outline.point(lambda value: 255 if value >= 128 else 0))
    output = io.BytesIO(); guide.save(output,"PNG"); return output.getvalue()


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


def automatic_feather(bounds, crop, canvas_content, minimum=8):
    """Scale the transition to the selection and model resolution, inside its boundary."""
    extent = max(bounds[2]-bounds[0],bounds[3]-bounds[1])
    source_per_model_pixel = max(crop[2]/canvas_content[2],crop[3]/canvas_content[3])
    # Leave a fully edited core in narrow selections instead of fading it away.
    short_side=min(bounds[2]-bounds[0],bounds[3]-bounds[1])
    return min(64,max(0,(short_side-1)//4),max(minimum,round(extent*.02),round(source_per_model_pixel*5)))


def _smooth_field(values, radius):
    """Three separable box passes approximate a Gaussian on the small color grid."""
    for _ in range(3):
        for axis in (0,1):
            padding=[(0,0)]*values.ndim;padding[axis]=(radius,radius)
            padded=np.pad(values,padding,mode="edge")
            cumulative=np.cumsum(padded,axis=axis,dtype=np.float32)
            shape=list(cumulative.shape);shape[axis]=1
            cumulative=np.concatenate((np.zeros(shape,dtype=np.float32),cumulative),axis=axis)
            before=[slice(None)]*values.ndim;after=before.copy()
            before[axis]=slice(None,-(2*radius+1));after[axis]=slice(2*radius+1,None)
            values=(cumulative[tuple(after)]-cumulative[tuple(before)])/(2*radius+1)
    return values


def match_local_colors(original, patch, mask):
    """Infer a smooth local color offset from unchanged context, including corners.

    Normalized convolution carries reliable context into nearby painted pixels.
    Corrections fade where context is absent, and never sample the edited object.
    """
    target,candidate,context=_context_samples(original,patch,mask)
    if context.sum()<128 or context.mean()<.05:
        return patch,{"applied":False,"reason":"insufficient context"}
    difference=np.asarray(target,dtype=np.float32)-np.asarray(candidate,dtype=np.float32)
    # Large mismatches are more likely replaced geometry than a local color cast.
    reliable=context & (np.abs(difference).mean(axis=2)<=48) & (np.abs(difference).max(axis=2)<=72)
    if reliable.sum()<128:
        return patch,{"applied":False,"reason":"insufficient matching surroundings"}
    weighted=np.concatenate((difference*reliable[:,:,None],reliable[:,:,None]),axis=2)
    blurred=_smooth_field(weighted,max(2,round(max(target.size)*.025)))
    support=np.maximum(blurred[:,:,3],0)
    correction=np.clip(blurred[:,:,:3]/np.maximum(support[:,:,None],.001),-32,32)
    correction *= np.minimum(1,support[:,:,None]/.15)
    before=float(np.abs(difference[reliable]).mean())
    after=float(np.abs((difference-correction)[reliable]).mean())
    if before<1 or after>before*.8:
        return patch,{"applied":False,"reason":"no reliable improvement"}
    # One float channel at a time and bounded row blocks keep 4K memory modest.
    pixels=np.array(patch)
    for channel in range(3):
        field=Image.fromarray(correction[:,:,channel]).resize(patch.size,Image.Resampling.BILINEAR)
        for top in range(0,patch.height,128):
            bottom=min(top+128,patch.height)
            offset=np.asarray(field.crop((0,top,patch.width,bottom)))
            pixels[top:bottom,:,channel]=np.clip(np.rint(pixels[top:bottom,:,channel].astype(np.float32)+offset),0,255).astype(np.uint8)
    return Image.fromarray(pixels),{"applied":True,"context_error_before":round(before,2),"context_error_after":round(after,2),
                                  "max_offset":round(float(np.abs(correction).max()),2)}


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
        adaptive=selection.get("blend_mode")=="adaptive"
        if adaptive and selection.get("color_match",True):
            patch,diagnostics["local_color_match"]=match_local_colors(reference,patch,mask)
        feather=selection.get("blend_width",selection["feather"]) if adaptive else selection["feather"]
        diagnostics["blending"]={"mode":"adaptive" if adaptive else "manual","width":feather,
                                 "opaque_object":bool(selection.get("core_mask_id"))}
        original.paste(patch,(x,y),selection_alpha(directory,selection))
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
    x, y, width, height = selection["crop"]
    if selection.get("version") == 2:
        mask = selection_alpha(directory,selection)
    else:
        if selection["feather"]:
            mask = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(selection["feather"])))
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

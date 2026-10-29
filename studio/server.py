"""Phone-friendly local image library and generation API."""
import argparse
import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import signal
import ssl
import time
import uuid
from pathlib import Path

from aiohttp import web
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

from .engine import Engine, QUALITIES, make_thumbnail
from .storage import Store
from .media import media_file, save_jpeg
from .metrics import Metrics
from .downloads import Downloads
from .video import Videos
from .masking import normalize_mask, masked_size, automatic_feather
from .automask import AutoMask, AutomaticSelectionError, validate_selection
from .identity import CONCEPTS, WORKING_SIDE, blend_width as identity_blend, body_guide, close_up, clothing_area, donor_body, donor_crops, mask_png, swap_areas, request_parts
from .sizing import OUTPUT_SIZES, RESIZE_MODES, MAX_PIXELS, plan_size
from .closeup import DETAIL as CLOSE_UP_DETAIL, SIZES as CLOSE_UP_SIZES, close_up_aspect, close_up_prompt, close_up_size, crop_source

ROOT = Path(__file__).resolve().parents[1]
STATE = web.AppKey("studio", dict)
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
ACTIVE = ("queued", "submitted", "running")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
STATIC_FILES = ("app.js", "projects.js", "masking.js", "automatic.js", "actions.js", "identity.js", "closeup.js", "video.js", "settings.js", "setup.js",
                "style.css", "projects.css", "polish.css", "sharing.css", "offline.css", "viewer.css", "setup.css",
                "icon.svg", "icon-192.png", "icon-512.png", "icon-maskable.png", "apple-touch-icon.png", "manifest.json",
                "share-store.js", "sharing.js", "share-help.html", "share-help.js", "offline-store.js", "offline.js")


def failure(message, status=400):
    return web.json_response({"error": message}, status=status)


def needs_setup(request):
    """Phones reach the studio over HTTPS once HTTPS is ready; plain HTTP stays for this Mac."""
    return bool(request.app[STATE]["tls"]) and not request.secure and request.url.host not in LOOPBACK


@web.middleware
async def access(request, handler):
    state = request.app[STATE]
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            return failure("Open the studio directly to make changes.", 403)
    if needs_setup(request) and request.path.startswith(("/api/", "/media/")) and request.path != "/api/health":
        return failure("Open the secure studio address to continue.", 403)
    if request.path.startswith(("/api/", "/media/")) and request.path not in ("/api/auth", "/api/login", "/api/health", "/api/share-upload"):
        token = request.cookies.get("studio_session", "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if state["pin"] and not state["store"].one("SELECT token FROM sessions WHERE token=? AND expires>?", (digest, time.time())):
            return failure("Enter your studio PIN to continue.", 401)
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = failure(error.reason, error.status)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        response = failure("Check your request: " + str(error))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    response.headers.setdefault("Cache-Control", "no-store" if request.path.startswith("/api/") else "private, max-age=60")
    return response


async def index(request):
    if needs_setup(request):
        return web.Response(status=302, headers={"Location": "/setup", "Cache-Control": "no-store"})
    return web.FileResponse(STATIC / "index.html")


async def static_file(request):
    name = request.match_info["name"]
    if name not in STATIC_FILES:
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name)


async def service_worker(request):
    return web.FileResponse(STATIC / "sw.js", headers={"Cache-Control": "no-cache"})


async def setup_page(request):
    # The page checks whether this phone already trusts the HTTPS address, then opens it.
    return web.FileResponse(STATIC / "setup.html", headers={"Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self' https:; frame-ancestors 'none'"})


async def certificate_authority(request):
    tls = request.app[STATE]["tls"]
    if not tls:
        raise web.HTTPNotFound()
    # DER is the format both iOS profiles and Android's CA certificate installer accept.
    content = ssl.PEM_cert_to_DER_cert((tls / "ca.crt").read_text())
    return web.Response(body=content, content_type="application/x-x509-ca-cert", headers={"Cache-Control": "no-store"})


async def auth(request):
    state = request.app[STATE]
    token = request.cookies.get("studio_session", "")
    valid = not state["pin"] or bool(state["store"].one("SELECT token FROM sessions WHERE token=? AND expires>?",
                    (hashlib.sha256(token.encode()).hexdigest(), time.time())))
    return web.json_response({"authenticated": valid, "pin_required": bool(state["pin"])})


def check_pin(request, pin):
    """Both browser login and the phone Shortcut share the same attempt limit."""
    state = request.app[STATE]
    address = request.remote
    attempts = state["attempts"].setdefault(address, [])
    attempts[:] = [t for t in attempts if time.time() - t < 900]
    if len(attempts) >= 8:
        return failure("Too many attempts. Please wait 15 minutes.", 429)
    if not hmac.compare_digest(str(pin).encode(), state["pin"].encode()):
        attempts.append(time.time())
        return failure("That PIN doesn’t match. Try again.", 401)
    attempts.clear()


async def login(request):
    state = request.app[STATE]
    payload = await request.json()
    error = check_pin(request, payload.get("pin", ""))
    if error is not None:
        return error
    token = secrets.token_urlsafe(32)
    state["store"].execute("INSERT INTO sessions VALUES (?,?)", (hashlib.sha256(token.encode()).hexdigest(), time.time() + 30 * 86400))
    response = web.json_response({"authenticated": True})
    response.set_cookie("studio_session", token, httponly=True, secure=request.secure, samesite="Strict", max_age=30 * 86400)
    return response


async def logout(request):
    token = request.cookies.get("studio_session", "")
    request.app[STATE]["store"].execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = web.json_response({"ok": True})
    response.del_cookie("studio_session")
    return response


async def health(request):
    return web.json_response({"service": "local-image-studio", "ok": True})


async def options(request):
    state = request.app[STATE]
    status = await state["metrics"].status()
    return web.json_response({"qualities": QUALITIES, "output_sizes": OUTPUT_SIZES, "resize_modes": RESIZE_MODES,
                              "engine_ready": status["engine_ready"], "engine_state": status["engine_state"], "auto_start": status["auto_start"],
                              "lan_url": os.environ.get("STUDIO_LAN_URL", f"{request.scheme}://{request.host}"),
                              "pin_required": bool(state["pin"]), "max_variations": 4,
                              "automatic_masks": await state["automask"].status()})


async def automatic_mask(request):
    state = request.app[STATE]
    source = state["store"].one("SELECT * FROM uploads WHERE id=?", (request.match_info["id"],))
    if not source:
        raise web.HTTPNotFound()
    target, protect, skin = validate_selection(await request.json())
    engine = state["engine"]
    if engine.resource_lock.locked() or state["store"].one("SELECT id FROM jobs WHERE status IN ('submitted','running')"):
        return failure("Your Mac is making an image. Try automatic selection when it finishes.", 409)
    async with engine.resource_lock:
        try:
            result = await state["automask"].select(media_file(state["store"].directory, "uploads", source["id"]), target, protect, skin)
        except AutomaticSelectionError as error:
            return failure(str(error),error.status)
    return web.json_response(result)


async def save_mask(store, source, mask):
    """Store a server-made selection exactly like a painted one."""
    mask_id = str(uuid.uuid4())
    path = store.directory / "masks" / (mask_id + ".png")
    result = await asyncio.to_thread(lambda: normalize_mask(mask_png(mask, (source["width"], source["height"])), source, path))
    store.execute("INSERT INTO masks VALUES (?,?,?,?,?,?,?,?)", (mask_id, source["id"], time.time(), result["width"], result["height"],
                  json.dumps(result["bounds"]), result["sha256"], result["coverage"]))
    return dict(result, id=mask_id, source_id=source["id"], url=f"/media/mask/{mask_id}")


async def identity_areas(request):
    """Find the swap area in this photo and the reference crop in the other one, before generating."""
    state = request.app[STATE]
    store = state["store"]
    source = store.one("SELECT * FROM uploads WHERE id=?", (request.match_info["id"],))
    if not source:
        raise web.HTTPNotFound()
    payload = await request.json()
    parts = request_parts(payload)
    swap_mode = payload.get("mode")
    keep_object = payload.get("keep_object", "").strip() if isinstance(payload.get("keep_object", ""), str) else None
    if keep_object is None:
        return failure("Name the object to keep, such as glass or phone.")
    validate_selection({"target": keep_object, "protect_faces": True})
    donor = store.one("SELECT * FROM uploads WHERE id=?", (payload.get("donor_id"),)) if isinstance(payload.get("donor_id"), str) else None
    if not donor:
        return failure("Choose the photo to take the identity from.")
    if donor["sha256"] == source["sha256"]:
        return failure("Choose a different photo to take the identity from.")
    engine = state["engine"]
    if engine.resource_lock.locked() or store.one("SELECT id FROM jobs WHERE status IN ('submitted','running')"):
        return failure("Your Mac is making an image. Find the swap areas when it finishes.", 409)
    started = time.monotonic()
    async with engine.resource_lock:
        try:
            target = await state["automask"].select(media_file(store.directory, "uploads", source["id"]),
                                                  (keep_object or "object held in hand") if swap_mode else "", True, swap_mode == "whole", CONCEPTS)
            # A body guide also needs the reference's visible skin, for its skin tone.
            reference = await state["automask"].select(media_file(store.directory, "uploads", donor["id"]), "", True, body_guide(parts), CONCEPTS)
        except AutomaticSelectionError as error:
            return failure(str(error), error.status)
    try:
        areas = await asyncio.to_thread(swap_areas, target, parts, reference if swap_mode else None)
        crops = await asyncio.to_thread(donor_crops, reference, parts, donor["width"], donor["height"])
        head = await asyncio.to_thread(swap_areas, target, ["head"], reference) if swap_mode == "whole" else None
        if head:
            crops += await asyncio.to_thread(donor_crops, reference, ["head"], donor["width"], donor["height"])
        silhouette = await asyncio.to_thread(donor_body, reference, media_file(store.directory, "uploads", donor["id"])) if body_guide(parts) else None
    except ValueError as error:
        return failure(str(error))
    guide = None
    if silhouette:
        saved = await save_mask(store, donor, silhouette[0])
        guide = {"mask_id": saved["id"], "skin": silhouette[1]}
    area = await save_mask(store, source, areas["area"])
    core = await save_mask(store, source, areas["core"])
    head_pass = {"mask": await save_mask(store, source, head["area"] & areas["area"]),
                 "core": await save_mask(store, source, head["core"] & areas["area"])} if head else None
    clothing = clothing_area(target, areas["area"]) if head else None
    appearance = await save_mask(store, source, clothing) if clothing is not None and clothing.any() else None
    return web.json_response({"version": 2, "mode": swap_mode, "parts": parts, "keep_object": keep_object,
                              "head_pass": head_pass, "appearance_mask": appearance, "donor_id": donor["id"], "donor_crops": crops, "body_guide": guide, "mask": area, "core": core,
                              "summary": areas["summary"], "seconds": round(time.monotonic() - started, 1),
                              "cached": bool(target.get("cached") and reference.get("cached"))})


def normalized_upload(content, path, thumb):
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > MAX_PIXELS or image.width < 64 or image.height < 64:
            raise ValueError("Choose an image between 64 pixels and 16 megapixels.")
        if image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Choose a PNG, JPEG or WebP image.")
        width, height = ImageOps.exif_transpose(image).size
        if image.format == "JPEG" and image.mode == "RGB" and not image.getexif():
            # The studio's own JPEGs carry no EXIF and need no rotation; keep them exactly. Phone
            # photos are re-encoded, which also drops their location data.
            path.write_bytes(content)
        else:
            save_jpeg(ImageOps.exif_transpose(image), path)
    make_thumbnail(path, thumb)
    return width, height, hashlib.sha256(path.read_bytes()).hexdigest()


async def save_upload(state, content, name, create_project=False):
    upload_id = str(uuid.uuid4())
    directory = state["store"].directory
    path = directory / "uploads" / (upload_id + ".jpg")
    try:
        width, height, digest = await asyncio.to_thread(normalized_upload, content, path, directory / "thumbs" / (upload_id + ".webp"))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        path.unlink(missing_ok=True)
        raise ValueError("Choose a valid PNG, JPEG or WebP photo under 20 MB and 16 megapixels.")
    state["store"].execute("INSERT INTO uploads VALUES (?,?,?,?,?,?)", (upload_id, time.time(), name[:150], width, height, digest))
    result = {"id": upload_id, "name": name[:150], "width": width, "height": height, "url": f"/media/upload/{upload_id}", "thumbnail": f"/media/thumb/{upload_id}"}
    if create_project:
        result["project_id"] = state["store"].projects.create_upload(state["store"].one("SELECT * FROM uploads WHERE id=?", (upload_id,)))
        result["parent_version_id"] = upload_id
    return result


async def upload(request):
    reader = await request.multipart()
    part = await reader.next()
    if not part or part.name != "image":
        return failure("Choose an image to upload.")
    chunks = bytearray()
    while chunk := await part.read_chunk():
        chunks.extend(chunk)
        if len(chunks) > MAX_UPLOAD:
            return failure("Choose an image smaller than 20 MB.", 413)
    return web.json_response(await save_upload(request.app[STATE], bytes(chunks), part.filename or "Photo", create_project=True), status=201)


async def share_upload(request):
    """Shortcuts sends one converted image as a File body, with no browser cookies."""
    if not request.secure and request.url.host not in LOOPBACK:
        return failure("Use the secure studio address for sharing photos.", 403)
    error = check_pin(request, request.headers.get("X-Studio-PIN", ""))
    if error is not None:
        return error
    content = await request.read()
    if len(content) > MAX_UPLOAD:
        return failure("Choose an image smaller than 20 MB.", 413)
    result = await save_upload(request.app[STATE], content, "Shared photo", create_project=True)
    result["url"] = f"{request.scheme}://{request.host}/#project={result['project_id']}&image={result['id']}"
    return web.json_response(result, status=201)


async def share_target_fallback(request):
    # A newly installed/outdated worker may not intercept the share yet. Never
    # accept an unauthenticated upload here or silently drop it into the library.
    return web.Response(status=303, headers={"Location": "/?share-error=setup", "Cache-Control": "no-store"})


async def edit_source(request):
    state = request.app[STATE]
    image = state["store"].image(request.match_info["id"])
    if not image:
        raise web.HTTPNotFound()
    data = (state["store"].directory / "images" / image["filename"]).read_bytes()
    result = await save_upload(state, data, "Studio image")
    result["parent_image_id"] = image["id"]
    state["store"].projects.sync()
    member = state["store"].one("SELECT * FROM versions WHERE id=?", (image["id"],))
    state["store"].projects.bind_source(result["id"], member["project_id"], image["id"])
    result.update(project_id=member["project_id"], parent_version_id=image["id"])
    return web.json_response(result, status=201)


async def upload_mask(request):
    store = request.app[STATE]["store"]
    source_id = request.match_info["id"]
    source = store.one("SELECT * FROM uploads WHERE id=?", (source_id,))
    if not source:
        raise web.HTTPNotFound()
    reader = await request.multipart()
    part = await reader.next()
    if not part or part.name != "mask":
        return failure("Paint a selection mask first.")
    content = bytearray()
    while chunk := await part.read_chunk():
        content.extend(chunk)
        if len(content) > MAX_UPLOAD:
            return failure("Choose a smaller selection mask.", 413)
    mask_id = str(uuid.uuid4())
    path = store.directory / "masks" / (mask_id + ".png")
    try:
        result = await asyncio.to_thread(normalize_mask, bytes(content), source, path)
        store.execute("INSERT INTO masks VALUES (?,?,?,?,?,?,?,?)",
                      (mask_id, source_id, time.time(), result["width"], result["height"],
                       json.dumps(result["bounds"]), result["sha256"], result["coverage"]))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        path.unlink(missing_ok=True)
        raise ValueError("Paint a non-empty PNG mask matching this photo.")
    return web.json_response(dict(result, id=mask_id, source_id=source_id, url=f"/media/mask/{mask_id}"), status=201)


async def mask_detail(request):
    mask = request.app[STATE]["store"].one("SELECT * FROM masks WHERE id=?", (request.match_info["id"],))
    if not mask:
        raise web.HTTPNotFound()
    mask["bounds"] = json.loads(mask["bounds"])
    mask["url"] = f"/media/mask/{mask['id']}"
    return web.json_response(mask)


async def generate(request):
    state = request.app[STATE]
    payload = await request.json()
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 5000:
        return failure("Describe your image in 1–5,000 characters.")
    mode = payload.get("mode", "create")
    quality = payload.get("quality", "balanced")
    aspect = payload.get("aspect", "square")
    count = payload.get("variations", 1)
    seed = payload.get("seed")
    if mode not in ("create", "edit") or quality not in QUALITIES or aspect not in QUALITIES[quality]["sizes"]:
        return failure("Choose a valid mode, quality and shape.")
    if type(count) is not int or not 1 <= count <= 4:
        return failure("Choose between 1 and 4 variations.")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF):
        return failure("Seed must be a whole number between 0 and 4294967295.")
    if type(payload.get("snofs", True)) is not bool:
        return failure("Choose whether to use SNOFS.")
    source_id = payload.get("source_id")
    framed = payload.get("close_up")
    if framed is not None:
        prepared = state["store"].one("SELECT * FROM close_ups WHERE id=?", (source_id,)) if isinstance(source_id, str) else None
        if mode != "edit" or not isinstance(framed, dict) or not prepared:
            return failure("Frame the close-up again; its area was not prepared.")
        subject = framed.get("subject", "")
        if framed.get("detail", "faithful") not in CLOSE_UP_DETAIL or not isinstance(subject, str) or len(subject) > 200:
            return failure("Choose Faithful or More detail, and describe the subject in under 200 characters.")
        if payload.get("mask_id") is not None or payload.get("identity_swap") is not None:
            return failure("A close-up uses the framed area only.")
        prompt = close_up_prompt(subject.strip())
        aspect = close_up_aspect(json.loads(prepared["box"]))
        payload.update(output_size="original", resize_mode="native" if prepared["size"] == "standard" else "auto")
    source = state["store"].one("SELECT * FROM uploads WHERE id=?", (source_id,)) if mode == "edit" else None
    if mode == "edit" and not source:
        return failure("Upload a photo or choose an image from your library first.")
    if framed is not None and close_up_size(json.loads(prepared["box"]), prepared["size"], QUALITIES[quality]["sizes"]) != (source["width"], source["height"]):
        return failure("The quality changed after this frame was prepared. Frame the close-up again.")
    if source and not state["store"].projects.source(source_id):
        state["store"].projects.create_upload(source)
    binding = state["store"].projects.source(source_id) if source else None
    if payload.get("project_id") and (not binding or binding["project_id"] != payload["project_id"]):
        return failure("Choose an editing photo from this project.")
    parent_id = binding["parent_id"] if binding and state["store"].image(binding["parent_id"]) else None
    pending = state["store"].one("SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','submitted','running')")["count"]
    if pending + count > 24:
        return failure("Your queue is full. Wait for some images to finish before adding more.", 409)
    mask_id = payload.get("mask_id")
    mask = None
    protection = None
    core = None
    identity = payload.get("identity_swap")
    donor = None
    if identity is not None:
        if mode != "edit" or not isinstance(identity, dict) or mask_id is None or payload.get("mask_core_id") is None:
            return failure("Find the swap areas before swapping an identity.")
        if payload.get("mask_selection") is not None or payload.get("mask_preserve_shape") or payload.get("mask_protection_id") is not None:
            return failure("Identity swaps choose their own areas. Remove other selection settings.")
        parts = request_parts(identity)
        swap_mode = identity.get("mode")
        donor = state["store"].one("SELECT * FROM uploads WHERE id=?", (identity.get("donor_id"),)) if isinstance(identity.get("donor_id"), str) else None
        if not donor or donor["sha256"] == source["sha256"]:
            return failure("Choose a different photo to take the identity from.")
        crops = identity.get("donor_crops")
        if not isinstance(crops, list) or len(crops) != 1 + (swap_mode == "whole" or close_up(parts)) or any(
                not isinstance(crop, list) or len(crop) != 4 or any(type(v) is not int for v in crop) or crop[0] < 0 or crop[1] < 0 or
                crop[2] < 16 or crop[3] < 16 or crop[0] + crop[2] > donor["width"] or crop[1] + crop[3] > donor["height"] for crop in crops):
            return failure("Find the swap areas again; the reference crops are not valid.")
        guide = None
        if body_guide(parts):
            supplied = identity.get("body_guide")
            guide = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (supplied.get("mask_id"), donor["id"])) if isinstance(supplied, dict) and isinstance(supplied.get("mask_id"), str) else None
            skin = supplied.get("skin") if guide else None
            if not guide or not isinstance(skin, list) or len(skin) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in skin):
                return failure("Find the swap areas again; the body guide is missing.")
        notes = identity.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 2000:
            return failure("Keep identity swap notes under 2,000 characters.")
        keep_object = identity.get("keep_object", "")
        validate_selection({"target": keep_object, "protect_faces": True})
        head_masks = None
        appearance_mask = None
        if swap_mode == "whole":
            supplied = identity.get("head_pass")
            if not isinstance(supplied, dict):
                return failure("Preview the whole identity again to prepare the head detail pass.")
            supplied = supplied.get("masked_edit", supplied)  # saved recipes can be replayed, too
            if not isinstance(supplied, dict):
                return failure("Preview the whole identity again to prepare the head detail pass.")
            head_masks = [state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (supplied.get(key), source_id))
                          if isinstance(supplied.get(key), str) else None for key in ("mask_id", "core_mask_id")]
            if not all(head_masks):
                return failure("The head selection belongs to a different photo. Preview the areas again.")
            supplied_appearance = identity.get("appearance_mask")
            if supplied_appearance is not None:
                appearance_mask = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (supplied_appearance.get("mask_id"), source_id)) if isinstance(supplied_appearance, dict) and isinstance(supplied_appearance.get("mask_id"), str) else None
                if not appearance_mask:
                    return failure("The clothing guide belongs to a different photo. Preview the areas again.")
    if mask_id is not None:
        if mode != "edit" or not isinstance(mask_id, str):
            return failure("Select an editing photo and paint an area first.")
        mask = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (mask_id, source_id))
        if not mask:
            return failure("This selection belongs to a different photo. Paint it again.")
        if payload.get("mask_core_id") is not None:
            core = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (str(payload["mask_core_id"]), source_id))
            if not core:
                return failure("The object outline belongs to a different photo. Select the area again.")
        if type(payload.get("mask_preserve_shape",False)) is not bool:
            return failure("Choose whether to guide the edit with the original shape.")
        sizing = masked_size(source, json.loads(mask["bounds"]), payload.get("mask_feather", 8),
                             quality, aspect, QUALITIES, state["upscaler"],payload.get("mask_detail","balanced"),payload.get("mask_context","balanced"),
                             WORKING_SIDE[quality] if identity is not None else None)
        if any(type(payload.get(key,True)) is not bool for key in ("mask_color_match","mask_align")):
            return failure("Choose whether to match surrounding colors and align the patch.")
        if payload.get("mask_blend_mode","adaptive") not in ("adaptive","manual"):
            return failure("Choose automatic or manual edge blending.")
        if payload.get("mask_protection_id") is not None:
            protection = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (payload["mask_protection_id"], source_id))
            if not protection:
                return failure("The protected area belongs to a different photo. Select the area again.")
            with Image.open(state["store"].directory / "masks" / (mask_id + ".png")) as selected, Image.open(state["store"].directory / "masks" / (protection["id"] + ".png")) as protected:
                if ImageChops.multiply(selected, protected).getbbox():
                    return failure("The selection overlaps a protected area. Adjust the mask before editing.")
        selection = payload.get("mask_selection")
        if selection is not None:
            if not isinstance(selection, dict) or selection.get("mode") not in ("change", "move", "add") or type(selection.get("extra_room")) is not int or not 0 <= selection["extra_room"] <= 48:
                return failure("Choose valid automatic selection settings.")
            validate_selection({"target":selection.get("target", ""), "protect_faces":True, "protect_skin":selection.get("protect_skin",False)})
            if type(selection.get("protect_faces")) is not bool or any(selection.get(key) is not None and (not isinstance(selection[key],str) or len(selection[key])>200) for key in ("model","model_revision")):
                return failure("Choose valid face protection and selection model settings.")
            if selection.get("placement") is not None:
                box=selection["placement"]
                if not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box) or not 0<=box[0]<box[2]<=source["width"] or not 0<=box[1]<box[3]<=source["height"] or selection["mode"] not in ("add","move"):
                    return failure("Draw a valid placement box within this photo.")
        if payload.get("mask_preserve_shape") and (selection or {}).get("mode","change") != "change":
            return failure("Original-shape guidance is for changing an existing object. Turn it off to move or add something.")
    else:
        sizing = plan_size(quality, aspect, mode, source, payload.get("output_size", "preset"), QUALITIES,
                           state["upscaler"], payload.get("resize_mode", "auto"))
    model = QUALITIES[quality]["model"]
    # Identity uses the base model: a creative LoRA can retain or invent appearance.
    use_snofs = payload.get("snofs", True) and not (identity is not None and identity.get("mode")) and framed is None
    recipe = {"version": 2, "mode": mode, "prompt": prompt.strip(), "quality": quality, "aspect": aspect,
              **sizing, "model": model, "model_source": state["models"][model],
              "steps": 4, "guidance": 1.0, "snofs": bool(use_snofs),
              "adapter": state["adapter"] if use_snofs else None, "runtime": state["runtime"],
              "source_id": source_id if mode == "edit" else None,
              "source": {k: source[k] for k in ("name", "width", "height", "sha256")} if source else None,
              "parent_image_id": parent_id if mode == "edit" else None}
    if framed is not None:
        recipe["close_up"] = {"version": 1, "method": "photo_below_its_resolution_v1", "from": json.loads(prepared["box"]),
                              "size": prepared["size"], "scale": prepared["scale"], "detail": framed.get("detail", "faithful"),
                              "subject": subject.strip()}
    if mask:
        blend_mode=payload.get("mask_blend_mode","adaptive")
        blend_width=automatic_feather(json.loads(mask["bounds"]),sizing["processing"]["crop"],sizing["processing"]["canvas_content"]) if blend_mode=="adaptive" else payload.get("mask_feather",8)
        if donor and blend_mode=="adaptive":
            blend_width=identity_blend(parts,json.loads(mask["bounds"]),blend_width)
        recipe["masked_edit"] = {"version":2,"mask_id": mask_id, "sha256": mask["sha256"],
                                  "bounds": json.loads(mask["bounds"]), "crop": sizing["processing"]["crop"],
                                  "feather": payload.get("mask_feather", 8),
                                  "context_padding":sizing["processing"]["context_padding"],
                                  "canvas_content":sizing["processing"]["canvas_content"],
                                  "blend_mode":blend_mode,"blend_width":blend_width,
                                  "color_match":payload.get("mask_color_match",True),"align":payload.get("mask_align",True),
                                  "feather_method":"inward_cosine","method": "matched_canvas_context_blend", "preserve_unselected": True}
        if protection:
            recipe["masked_edit"]["protection_mask_id"] = protection["id"]
            recipe["masked_edit"]["protection_sha256"] = protection["sha256"]
        if core:
            # A short fade where the object meets protected skin, scaled like SAM's 1024px preview outlines.
            recipe["masked_edit"].update(core_mask_id=core["id"],core_sha256=core["sha256"],object_blending="opaque_core_v1",
                                         opaque_edge=max(2,round(1.5*max(source["width"],source["height"])/1024)))
        if payload.get("mask_preserve_shape"):
            recipe["masked_edit"]["shape_guidance"] = "outline_reference_v1"
        if payload.get("mask_selection"):
            recipe["masked_edit"]["selection"] = {key:payload["mask_selection"][key] for key in ("model","model_revision","target","mode","extra_room","protect_faces","protect_skin","placement") if key in payload["mask_selection"]}
            if recipe["masked_edit"]["selection"].get("placement"):
                recipe["masked_edit"]["placement_guidance"] = "visual_reference_v1"
    if donor:
        recipe["identity_swap"] = {"version": 2 if swap_mode else 1, "method": "person_then_head_v2" if head_masks else "masked_reference_v2" if swap_mode else "masked_reference_v1",
                                   "mode": swap_mode, "parts": parts, "notes": notes.strip(), "keep_object": keep_object.strip(),
                                   "donor_id": donor["id"], "donor_crops": crops,
                                   "donor": {k: donor[k] for k in ("name", "width", "height", "sha256")}}
        if guide:
            recipe["identity_swap"]["body_guide"] = {"mask_id": guide["id"], "sha256": guide["sha256"], "skin": skin}
        if appearance_mask:
            recipe["identity_swap"]["appearance_mask"] = {"mask_id": appearance_mask["id"], "sha256": appearance_mask["sha256"]}
        if head_masks:
            head_mask, head_core = head_masks
            # A second head pass may never repaint pixels outside the approved whole-person area.
            with Image.open(state["store"].directory / "masks" / (mask_id + ".png")) as outer, \
                 Image.open(state["store"].directory / "masks" / (head_mask["id"] + ".png")) as inner, \
                 Image.open(state["store"].directory / "masks" / (head_core["id"] + ".png")) as inner_core:
                if ImageChops.subtract(inner, outer).getbbox() or ImageChops.subtract(inner_core, inner).getbbox():
                    return failure("The head selection exceeds the preview. Preview the areas again.")
            bounds = json.loads(head_mask["bounds"])
            detail = masked_size(source, bounds, 8, quality, aspect, QUALITIES, state["upscaler"], working_side=WORKING_SIDE[quality])
            selection = dict(recipe["masked_edit"], mask_id=head_mask["id"], sha256=head_mask["sha256"], bounds=bounds,
                             core_mask_id=head_core["id"], core_sha256=head_core["sha256"],
                             crop=detail["processing"]["crop"], canvas_content=detail["processing"]["canvas_content"],
                             context_padding=detail["processing"]["context_padding"], color_match=False, align=False,
                             blend_width=automatic_feather(bounds, detail["processing"]["crop"], detail["processing"]["canvas_content"]))
            recipe["identity_swap"]["head_pass"] = {"sizing": detail, "masked_edit": selection}
    base_seed = seed if seed is not None else secrets.randbits(32)
    recipes = [dict(recipe, seed=(base_seed + i) % 2**32) for i in range(count)]
    result = state["store"].add_jobs(recipes)
    state["engine"].wake.set()
    return web.json_response(result, status=202)


async def jobs(request):
    store = request.app[STATE]["store"]
    rows = store.rows("SELECT id FROM jobs WHERE status IN ('queued','submitted','running') OR created>? ORDER BY created DESC,variation LIMIT 100", (time.time() - 86400,))
    estimates = request.app[STATE]["metrics"].queue()
    return web.json_response({"jobs": [dict(store.job(row["id"]), estimate=estimates.get(row["id"])) for row in rows]})


async def project_library(request):
    section = request.query.get("section","library")
    if section not in ("library","favorites","archive"):
        return failure("Choose Library, Favorites or Archive.")
    result = request.app[STATE]["store"].projects.library(section,request.query.get("q","")[:200],
                  max(0,int(request.query.get("offset",0))), min(1000,max(1,int(request.query.get("limit",48)))))
    return web.json_response(result)


async def project_detail(request):
    project = request.app[STATE]["store"].projects.detail(request.match_info["id"])
    if not project:
        raise web.HTTPNotFound()
    return web.json_response(project)


async def offline_copy(request):
    """The whole library in one response, so the installed phone app can browse it away from home."""
    state = request.app[STATE]
    store = state["store"]
    store.projects.sync()
    projects = [store.projects.detail(row["id"]) for row in store.rows("SELECT id FROM projects")]
    body = json.dumps({"projects": [project for project in projects if project["versions"]],
                       "videos": state["videos"].entries()}, separators=(",", ":"))
    tag = '"%s"' % hashlib.sha256(body.encode()).hexdigest()[:32]
    if request.headers.get("If-None-Match") == tag:
        return web.Response(status=304, headers={"ETag": tag})
    return web.Response(text=body, content_type="application/json", headers={"ETag": tag})


async def photos(request):
    section = request.query.get("section", "library")
    if section not in ("library", "archive"):
        return failure("Choose Library or Archive.")
    return web.json_response(request.app[STATE]["store"].projects.images(section, request.query.get("q", "")[:200],
        max(0, int(request.query.get("offset", 0))), min(1000, max(1, int(request.query.get("limit", 48))))))


async def favorites(request):
    return web.json_response(request.app[STATE]["store"].projects.favorites(
        request.query.get("q", "")[:200], max(0, int(request.query.get("offset", 0))),
        min(1000, max(1, int(request.query.get("limit", 48))))))


async def update_project(request):
    store = request.app[STATE]["store"]
    project_id = request.match_info["id"]
    if not store.projects.detail(project_id):
        raise web.HTTPNotFound()
    payload = await request.json()
    if not isinstance(payload,dict) or not payload or set(payload)-{"favorite","archived"} or any(type(v) is not bool for v in payload.values()):
        return failure("Choose favorite or archive as true or false.")
    with store.db:
        if "archived" in payload:
            store.db.execute("UPDATE projects SET archived=? WHERE id=?", (int(payload["archived"]), project_id))
        if "favorite" in payload:
            for version in store.projects.detail(project_id)["versions"]:
                store.projects.set_favorite(version["id"], payload["favorite"])
    return web.json_response(store.projects.detail(project_id))


async def project_edit_source(request):
    state = request.app[STATE]
    version = state["store"].projects.version(request.match_info["version_id"])
    if not version or version["project_id"] != request.match_info["id"] or not version["ready"]:
        raise web.HTTPNotFound()
    if version["kind"] == "image":
        content = media_file(state["store"].directory, "images", version["id"]).read_bytes()
        result = await save_upload(state,content,"Studio image")
        state["store"].projects.bind_source(result["id"],version["project_id"],version["id"])
        result["parent_image_id"] = version["id"]
    else:
        upload = state["store"].one("SELECT * FROM uploads WHERE id=?", (version["id"],))
        result = {"id":upload["id"],"name":upload["name"],"width":upload["width"],"height":upload["height"],
                  "url":version["url"],"thumbnail":version["thumbnail"]}
    result.update(project_id=version["project_id"],parent_version_id=version["id"])
    return web.json_response(result, status=201)


async def project_close_up_source(request):
    """The chosen area of an image, enlarged to the close-up's size: the model's input and the comparison."""
    state = request.app[STATE]
    store = state["store"]
    version = store.projects.version(request.match_info["version_id"])
    if not version or version["project_id"] != request.match_info["id"] or not version["ready"]:
        raise web.HTTPNotFound()
    payload = await request.json()
    box, size, quality = payload.get("box"), payload.get("size", "standard"), payload.get("quality", "balanced")
    if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box) or box[0] < 0 or box[1] < 0 or \
            box[2] < 32 or box[3] < 32 or box[0] + box[2] > version["width"] or box[1] + box[3] > version["height"]:
        return failure("Frame an area of at least 32 × 32 pixels inside the photo.")
    if max(box[2] / box[3], box[3] / box[2]) > 4:
        return failure("Frame an area no longer than four times its width.")
    if size not in CLOSE_UP_SIZES or quality not in QUALITIES:
        return failure("Choose Standard, 2K or 4K and a valid quality.")
    dimensions = close_up_size(box, size, QUALITIES[quality]["sizes"])
    path = media_file(store.directory, "uploads" if version["kind"] == "upload" else "images", version["id"])
    image = await asyncio.to_thread(crop_source, path, box, dimensions)
    content = io.BytesIO()
    save_jpeg(image, content)
    result = await save_upload(state, content.getvalue(), "Close-up source")
    store.projects.bind_source(result["id"], version["project_id"], version["id"])
    store.execute("INSERT INTO close_ups VALUES (?,?,?,?,?)", (result["id"], version["id"], json.dumps(box), size, dimensions[0] / box[2]))
    result.update(project_id=version["project_id"], parent_version_id=version["id"], parent_image_id=version["id"] if version["kind"] == "image" else None)
    return web.json_response(result, status=201)


async def update_version(request):
    store = request.app[STATE]["store"]
    version = store.projects.version(request.match_info["version_id"])
    if not version or version["project_id"] != request.match_info["id"] or not version["ready"]:
        raise web.HTTPNotFound()
    payload = await request.json()
    if not isinstance(payload,dict) or set(payload) != {"favorite"} or type(payload["favorite"]) is not bool:
        return failure("Choose favorite as true or false.")
    with store.db:
        store.projects.set_favorite(version["id"], payload["favorite"])
    return web.json_response(store.projects.version(version["id"]))


async def delete_project(request):
    store = request.app[STATE]["store"]
    if not store.projects.detail(request.match_info["id"]):
        raise web.HTTPNotFound()
    payload = await request.json()
    if not isinstance(payload,dict) or payload.get("confirm") is not True:
        return failure("Confirm permanent deletion of the whole project first.")
    try:
        store.projects.delete(request.match_info["id"])
    except ValueError as error:
        return failure(str(error),409)
    return web.json_response({"ok":True})


async def delete_version(request):
    store = request.app[STATE]["store"]
    project = store.projects.detail(request.match_info["id"])
    version = store.projects.version(request.match_info["version_id"])
    if not project or not version or version["project_id"] != project["id"]:
        raise web.HTTPNotFound()
    if version["status"] in ACTIVE:
        return failure("Stop the queued image first, or wait for it to finish.",409)
    payload = await request.json()
    if not isinstance(payload,dict) or payload.get("confirm") is not True:
        return failure("Confirm deletion of this image first.")
    await delete_versions(request.app[STATE], [version])
    return web.json_response({"ok":True})


async def delete_versions(state, versions):
    store = state["store"]
    # Keep a comparison copy of generated originals before hiding just that image.
    for version in versions:
        project = store.projects.detail(version["project_id"])
        if version["id"] == project["root_id"] and version["ready"]:
            if version["kind"] == "image" and not store.one(
                    "SELECT id FROM project_sources WHERE project_id=? AND parent_id=? AND id NOT IN (SELECT id FROM close_ups)", (project["id"], version["id"])):
                source = await save_upload(state, media_file(store.directory, "images", version["id"]).read_bytes(), "Studio image")
                store.projects.bind_source(source["id"], project["id"], version["id"])
            elif version["kind"] == "upload":
                store.projects.bind_source(version["id"], project["id"], version["id"])
    store.delete_images([version["id"] for version in versions])
    empty = [project_id for project_id in {v["project_id"] for v in versions}
             if not store.projects.detail(project_id)["versions"]]
    if empty:
        store.projects.delete_many(empty)


def bulk_selection(store, payload):
    if not isinstance(payload, dict) or payload.get("scope") not in ("projects", "images"):
        raise ValueError("Select projects or images first.")
    ids = payload.get("ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(value, str) for value in ids):
        raise ValueError("Select between 1 and 500 entries.")
    ids = list(dict.fromkeys(ids))
    for value in ids:
        uuid.UUID(value)
    versions = []
    for value in ids:
        if payload["scope"] == "projects":
            project = store.projects.detail(value)
            if not project:
                raise ValueError("A selected project no longer exists. Refresh your selection.")
            versions.extend(project["versions"])
        else:
            version = store.projects.version(value)
            if not version:
                raise ValueError("A selected image no longer exists. Refresh your selection.")
            versions.append(version)
    return ids, versions


async def bulk_action(request):
    state = request.app[STATE]
    store = state["store"]
    payload = await request.json()
    ids, versions = bulk_selection(store, payload)
    if payload.get("action") == "favorite" and type(payload.get("favorite")) is bool:
        with store.db:
            for version in versions:
                store.projects.set_favorite(version["id"], payload["favorite"])
    elif payload.get("action") == "delete":
        if payload.get("confirm") is not True:
            return failure("Confirm permanent deletion first.")
        if any(v["status"] in ACTIVE for v in versions):
            return failure("Stop selected queued or running images before deleting.", 409)
        if payload["scope"] == "projects":
            store.projects.delete_many(ids)
        else:
            await delete_versions(state, versions)
    else:
        return failure("Choose favorite or delete.")
    return web.json_response({"ok": True, "entries": len(ids), "images": len(versions)})


async def bulk_download(request):
    state = request.app[STATE]
    payload = await request.json()
    _, versions = bulk_selection(state["store"], payload)
    image_format = payload.get("format", "png")
    if image_format not in ("png", "jpg"):
        return failure("Choose PNG or JPG.")
    if payload["scope"] == "images" and any(not v["ready"] for v in versions):
        return failure("Selected images must finish before downloading.", 409)
    versions = [v for v in versions if v["ready"]]
    if not versions:
        return failure("There are no finished images to download.", 409)
    files = [(f'project-{v["project_id"][:8]}/studio-{v["id"]}',
              media_file(state["store"].directory, "uploads" if v["kind"] == "upload" else "images", v["id"])) for v in versions]
    try:
        result = await state["downloads"].create(files, image_format, jpeg_download)
    except FileNotFoundError:
        return failure("A selected image was removed. Refresh and try again.", 409)
    return web.json_response(result)


async def download_archive(request):
    entry = request.app[STATE]["downloads"].get(request.match_info["token"])
    if not entry:
        return failure("This download expired. Select the images again.", 404)
    return web.FileResponse(entry["path"], headers={"Content-Type": "application/zip",
                            "Content-Disposition": 'attachment; filename="studio-images.zip"'})


async def estimate(request):
    state = request.app[STATE]
    payload = await request.json()
    quality,aspect,mode = payload.get("quality","balanced"),payload.get("aspect","square"),payload.get("mode","create")
    if quality not in QUALITIES or aspect not in QUALITIES[quality]["sizes"] or mode not in ("create","edit"):
        return failure("Choose a valid quality and shape.")
    source = state["store"].one("SELECT * FROM uploads WHERE id=?", (payload.get("source_id"),)) if mode == "edit" else None
    if mode == "edit" and payload.get("version_id"):
        version = state["store"].projects.version(payload["version_id"])
        if version and version["ready"] and version["project_id"] == payload.get("project_id"):
            source = {"width":version["width"],"height":version["height"]}
    if mode == "edit" and not source:
        return failure("Choose a photo first.")
    if payload.get("mask_bounds") is not None:
        if mode != "edit":
            return failure("Selected-area editing requires a photo.")
        if type(payload.get("identity_swap",False)) is not bool:
            return failure("Choose whether this is an identity swap.")
        size = masked_size(source,payload["mask_bounds"],payload.get("mask_feather",8),quality,aspect,QUALITIES,state["upscaler"],payload.get("mask_detail","balanced"),payload.get("mask_context","balanced"),
                           WORKING_SIDE[quality] if payload.get("identity_swap") else None)
    else:
        size = plan_size(quality,aspect,mode,source,payload.get("output_size","preset"),QUALITIES,state["upscaler"],payload.get("resize_mode","auto"))
    recipe = dict(size,mode=mode,model=QUALITIES[quality]["model"],snofs=payload.get("snofs",True))
    if any(type(payload.get(key,False)) is not bool for key in ("mask_placement_guidance","mask_shape_guidance")):
        return failure("Choose valid placement or shape guidance.")
    if payload.get("mask_bounds") is not None:
        if payload.get("identity_swap"):
            swap_mode = payload.get("identity_mode")
            if swap_mode is not None:
                request_parts({"mode": swap_mode})
            recipe["identity_swap"]={"mode": swap_mode}
            if swap_mode:
                recipe["snofs"] = False
        elif payload.get("mask_placement_guidance"):
            recipe["masked_edit"]={"placement_guidance":"visual_reference_v1"}
        elif payload.get("mask_shape_guidance"):
            recipe["masked_edit"]={"shape_guidance":"outline_reference_v1"}
    prediction = state["metrics"].estimate(recipe)
    queue = state["metrics"].queue()
    last = list(queue.values())[-1] if queue else None
    prediction.update(queue_low=last["remaining_low"] if last else 0,queue_high=last["remaining_high"] if last else 0)
    return web.json_response(prediction)


async def metrics(request):
    return web.json_response(await request.app[STATE]["metrics"].status())


async def cancel(request):
    store = request.app[STATE]["store"]
    job = store.job(request.match_info["id"])
    if not job:
        raise web.HTTPNotFound()
    if job["status"] in ACTIVE:
        values = {"cancel_requested": 1}
        if not job["prompt_id"]:
            values.update(status="cancelled", phase="Stopped", finished=time.time())
        store.update_job(job["id"], **values)
        request.app[STATE]["engine"].wake.set()
    return web.json_response({"ok": True})


async def library(request):
    store = request.app[STATE]["store"]
    section = request.query.get("section", "library")
    offset = max(0, int(request.query.get("offset", 0)))
    limit = min(1000, max(1, int(request.query.get("limit", 48))))
    if section not in ("library", "favorites", "archive"):
        return failure("Choose Library, Favorites or Archive.")
    where = {"library": "archived=0", "favorites": "favorite=1 AND archived=0", "archive": "archived=1"}[section]
    query = request.query.get("q", "").strip()[:200]
    args = []
    if query:
        where += " AND instr(lower(json_extract(recipe,'$.prompt')),lower(?))>0"
        args.append(query)
    total = store.one("SELECT COUNT(*) AS count FROM images WHERE " + where, args)["count"]
    rows = store.rows("SELECT id FROM images WHERE " + where + " ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    counts = store.one("SELECT COUNT(*) AS total,COALESCE(SUM(archived=0),0) AS library,COALESCE(SUM(favorite=1 AND archived=0),0) AS favorites,COALESCE(SUM(archived=1),0) AS archive FROM images")
    return web.json_response({"images": [store.image(row["id"]) for row in rows], "total": total, "counts": counts})


async def image_detail(request):
    image = request.app[STATE]["store"].image(request.match_info["id"])
    if not image:
        raise web.HTTPNotFound()
    return web.json_response(image)


async def update_image(request):
    store = request.app[STATE]["store"]
    image_id = request.match_info["id"]
    if not store.image(image_id):
        raise web.HTTPNotFound()
    payload = await request.json()
    if not payload or set(payload) - {"favorite", "archived"} or any(type(v) is not bool for v in payload.values()):
        return failure("Choose favorite or archive as true or false.")
    with store.db:
        store.db.execute(f"UPDATE images SET {','.join(k+'=?' for k in payload)} WHERE id=?", (*map(int, payload.values()), image_id))
        if "favorite" in payload:
            store.db.execute("UPDATE versions SET favorite=? WHERE id=?", (int(payload["favorite"]), image_id))
    return web.json_response(store.image(image_id))


async def recipe_file(request):
    image = request.app[STATE]["store"].image(request.match_info["id"])
    if not image:
        raise web.HTTPNotFound()
    return web.Response(text=json.dumps(image["recipe"], indent=2), content_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="studio-{image["id"]}-settings.json"'})


async def delete_image(request):
    store = request.app[STATE]["store"]
    image_id = request.match_info["id"]
    if not store.image(image_id):
        raise web.HTTPNotFound()
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("confirm") is not True:
        return failure("Confirm permanent deletion of this image first.")
    job = store.job(image_id)
    if job and job["status"] in ACTIVE:
        return failure("Wait for this image to finish before deleting it.", 409)
    store.delete_image(image_id)
    return web.json_response({"ok": True, "deleted_id": image_id})


def jpeg_download(path):
    """A sharing copy at the saved dimensions; photos saved as JPEG are shared as they are."""
    if path.suffix == ".jpg":
        return path.read_bytes()
    with Image.open(path) as original:
        profile = original.info.get("icc_profile")
        if original.mode in ("RGBA", "LA") or "transparency" in original.info:
            rgba = original.convert("RGBA")
            image = Image.new("RGB", original.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = original.convert("RGB")
        output = io.BytesIO()
        image.save(output, "JPEG", quality=95, subsampling=0, optimize=True, icc_profile=profile)
        return output.getvalue()


async def media(request):
    state = request.app[STATE]
    media_id = request.match_info["id"]
    try:
        uuid.UUID(media_id)
    except ValueError:
        raise web.HTTPNotFound()
    kind = request.match_info["kind"]
    folders = {"image": "images", "upload": "uploads", "thumb": "thumbs", "mask": "masks"}
    if kind not in folders:
        raise web.HTTPNotFound()
    image_exists = state["store"].one("SELECT id FROM images WHERE id=?", (media_id,))
    upload_exists = state["store"].one("SELECT id FROM uploads WHERE id=?", (media_id,))
    mask_exists = state["store"].one("SELECT id FROM masks WHERE id=?", (media_id,)) if kind == "mask" else None
    if not {"image": image_exists, "upload": upload_exists, "thumb": image_exists or upload_exists, "mask": mask_exists}[kind]:
        raise web.HTTPNotFound()
    directory = state["store"].directory
    path = (media_file(directory, folders[kind], media_id) if kind in ("image", "upload")
            else directory / folders[kind] / (media_id + (".webp" if kind == "thumb" else ".png")))
    if not path.is_file():
        raise web.HTTPNotFound()
    output_format = request.query.get("format")
    if output_format is not None and (kind not in ("image", "upload") or output_format not in ("png", "jpg", "jpeg")):
        return failure("Choose PNG or JPG for a full-size image download.")
    headers = {}
    jpeg = output_format in ("jpg", "jpeg") and path.suffix != ".jpg"
    if output_format == "png" and path.suffix != ".png":
        return failure("This photo is saved as JPG. Download it as JPG.")
    if request.query.get("download") == "1":
        extension = ".jpg" if jpeg else path.suffix
        headers["Content-Disposition"] = f'attachment; filename="studio-{media_id}{extension}"'
    if jpeg:
        try:
            content = await asyncio.to_thread(jpeg_download, path)
        except FileNotFoundError:
            raise web.HTTPNotFound()
        return web.Response(body=content, content_type="image/jpeg", headers=headers)
    return web.FileResponse(path, headers=headers)


def create_app(data_dir=None, engine_url=None, project_root=None, pin=None, start_worker=True, tls_dir=None):
    directory = Path(data_dir or os.environ.get("STUDIO_DATA", ROOT / "studio-data"))
    tls = Path(tls_dir or os.environ.get("STUDIO_TLS_DIR", ROOT / "runtime/tls"))
    store = Store(directory)
    pin_path = directory / "access-pin.txt"
    if pin is None:
        if not pin_path.exists():
            descriptor = os.open(pin_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as file:
                file.write(f"{secrets.randbelow(1_000_000):06d}\n")
        pin = pin_path.read_text().strip()
    templates = {"create": json.loads((ROOT / "workflows/01 - Create Images.json").read_text()),
                 "edit": json.loads((ROOT / "workflows/02 - Edit Images.json").read_text())}
    engine = Engine(store, engine_url or os.environ.get("COMFY_URL", "http://127.0.0.1:8188"),
                    project_root or os.environ.get("ENGINE_PROJECT_ROOT", str(ROOT)), templates)
    app = web.Application(middlewares=[access], client_max_size=MAX_UPLOAD + 65536)
    app[STATE] = {"store": store, "engine": engine, "pin": pin, "attempts": {}, "tls": tls if (tls / "server.crt").exists() else None,
                  "models": json.loads((ROOT / "config/models.json").read_text()),
                  "adapter": json.loads((ROOT / "config/loras.json").read_text())["snofs-klein-9b-v1.4"],
                  "upscaler": json.loads((ROOT / "config/upscaler.json").read_text()),
                  "runtime": json.loads((ROOT / "config/runtime.json").read_text())}
    app[STATE]["metrics"] = Metrics(store,engine)
    app[STATE]["automask"] = AutoMask()
    app[STATE]["downloads"] = Downloads()
    videos = Videos(store, engine)
    app[STATE]["videos"] = videos
    app.cleanup_ctx.append(app[STATE]["downloads"].context)
    if start_worker:
        app.cleanup_ctx.append(engine.context)
        app.cleanup_ctx.append(videos.context)
    async def close_store(app):
        store.close()
    app.on_cleanup.append(close_store)
    app.add_routes([
        web.get("/", index), web.get("/static/{name}", static_file), web.get("/sw.js", service_worker),
        web.post("/share-target", share_target_fallback), web.post("/api/share-upload", share_upload),
        web.get("/setup", setup_page), web.get("/setup/image-studio-home.crt", certificate_authority),
        web.get("/api/auth", auth), web.post("/api/login", login), web.post("/api/logout", logout),
        web.get("/api/health", health), web.get("/api/options", options),
        web.get("/api/videos/options", videos.options), web.get("/api/videos", videos.library),
        web.post("/api/videos", videos.submit), web.post("/api/videos/{id}/cancel", videos.cancel), web.delete("/api/videos/{id}", videos.delete),
        web.get("/api/videos/{id}/recipe", videos.recipe), web.get("/media/video/{id}", videos.media),
        web.get("/api/favorites", favorites), web.get("/api/photos", photos), web.post("/api/bulk", bulk_action), web.get("/api/offline", offline_copy),
        web.post("/api/bulk-download", bulk_download), web.get("/api/downloads/{token}", download_archive),
        web.get("/api/projects",project_library), web.get("/api/projects/{id}",project_detail),
        web.patch("/api/projects/{id}",update_project), web.delete("/api/projects/{id}",delete_project),
        web.post("/api/projects/{id}/versions/{version_id}/edit-source",project_edit_source),
        web.post("/api/projects/{id}/versions/{version_id}/close-up",project_close_up_source),
        web.patch("/api/projects/{id}/versions/{version_id}",update_version),
        web.delete("/api/projects/{id}/versions/{version_id}",delete_version),
        web.post("/api/estimate",estimate), web.get("/api/metrics",metrics),
        web.post("/api/uploads/{id}/mask",upload_mask), web.get("/api/masks/{id}",mask_detail),
        web.post("/api/uploads/{id}/auto-mask",automatic_mask), web.post("/api/uploads/{id}/identity-areas",identity_areas),
        web.post("/api/uploads", upload), web.post("/api/generate", generate),
        web.get("/api/jobs", jobs), web.post("/api/jobs/{id}/cancel", cancel),
        web.get("/api/images", library), web.get("/api/images/{id}", image_detail),
        web.delete("/api/images/{id}", delete_image),
        web.patch("/api/images/{id}", update_image), web.post("/api/images/{id}/edit-source", edit_source),
        web.get("/api/images/{id}/recipe", recipe_file), web.get("/media/{kind}/{id}", media),
    ])
    return app


def tls_context(directory):
    """Serve HTTPS and switch to a renewed certificate on the next connection, without a restart."""
    certificate, key = directory / "server.crt", directory / "server.key"
    def load():
        stamp = certificate.stat().st_mtime_ns
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certificate, key)
        return stamp, context
    current = [load()]
    def renew(connection, server_name, initial):
        # Runs for every handshake, including phones that connect by IP address and send no name.
        try:
            if certificate.stat().st_mtime_ns != current[0][0]:
                current[0] = load()
        except (OSError, ssl.SSLError):
            pass  # Keep the previous certificate if the new one is unreadable.
        if current[0][1] is not initial:
            connection.context = current[0][1]
    current[0][1].sni_callback = renew
    return current[0][1]


async def serve(app, host, port, https_port):
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
        addresses = f"http://{host}:{port}"
        if app[STATE]["tls"]:
            await web.TCPSite(runner, host, https_port, ssl_context=tls_context(app[STATE]["tls"])).start()
            addresses += f" and https://{host}:{https_port}"
        print(f"Serving {addresses}", flush=True)
        stopped = asyncio.Event()
        for number in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(number, stopped.set)
        await stopped.wait()
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("STUDIO_PORT", "7878")))
    parser.add_argument("--https-port", type=int, default=7443)
    args = parser.parse_args()
    app = create_app()
    print(f"Studio PIN: {app[STATE]['pin']} (saved in {app[STATE]['store'].directory / 'access-pin.txt'})", flush=True)
    asyncio.run(serve(app, args.host, args.port, args.https_port))


if __name__ == "__main__":
    main()

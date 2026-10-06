"""Phone-friendly local image library and generation API."""
import argparse
import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from aiohttp import web
from PIL import Image, ImageOps, UnidentifiedImageError

from .engine import Engine, QUALITIES, make_thumbnail
from .storage import Store
from .metrics import Metrics
from .downloads import Downloads
from .masking import normalize_mask, masked_size
from .sizing import OUTPUT_SIZES, RESIZE_MODES, MAX_PIXELS, plan_size

ROOT = Path(__file__).resolve().parents[1]
STATE = web.AppKey("studio", dict)
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
ACTIVE = ("queued", "submitted", "running")


def failure(message, status=400):
    return web.json_response({"error": message}, status=status)


@web.middleware
async def access(request, handler):
    state = request.app[STATE]
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            return failure("Open the studio directly to make changes.", 403)
    if request.path.startswith(("/api/", "/media/")) and request.path not in ("/api/auth", "/api/login", "/api/health"):
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
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "private, max-age=60"
    return response


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def static_file(request):
    name = request.match_info["name"]
    if name not in ("app.js", "projects.js", "masking.js", "actions.js", "style.css", "projects.css", "masking.css", "polish.css", "icon.svg", "manifest.json"):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name)


async def auth(request):
    state = request.app[STATE]
    token = request.cookies.get("studio_session", "")
    valid = not state["pin"] or bool(state["store"].one("SELECT token FROM sessions WHERE token=? AND expires>?",
                    (hashlib.sha256(token.encode()).hexdigest(), time.time())))
    return web.json_response({"authenticated": valid, "pin_required": bool(state["pin"])})


async def login(request):
    state = request.app[STATE]
    payload = await request.json()
    address = request.remote
    attempts = state["attempts"].setdefault(address, [])
    attempts[:] = [t for t in attempts if time.time() - t < 900]
    if len(attempts) >= 8:
        return failure("Too many attempts. Please wait 15 minutes.", 429)
    if not hmac.compare_digest(str(payload.get("pin", "")), state["pin"]):
        attempts.append(time.time())
        return failure("That PIN doesn’t match. Try again.", 401)
    attempts.clear()
    token = secrets.token_urlsafe(32)
    state["store"].execute("INSERT INTO sessions VALUES (?,?)", (hashlib.sha256(token.encode()).hexdigest(), time.time() + 30 * 86400))
    response = web.json_response({"authenticated": True})
    response.set_cookie("studio_session", token, httponly=True, samesite="Strict", max_age=30 * 86400)
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
    return web.json_response({"qualities": QUALITIES, "output_sizes": OUTPUT_SIZES, "resize_modes": RESIZE_MODES, "engine_ready": await state["engine"].available(),
                              "lan_url": os.environ.get("STUDIO_LAN_URL", f"{request.scheme}://{request.host}"),
                              "pin_required": bool(state["pin"]), "max_variations": 4})


def normalized_upload(content, path, thumb):
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > MAX_PIXELS or image.width < 64 or image.height < 64:
            raise ValueError("Choose an image between 64 pixels and 16 megapixels.")
        if image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Choose a PNG, JPEG or WebP image.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        image.save(path, "PNG")
    make_thumbnail(path, thumb)
    return width, height, hashlib.sha256(path.read_bytes()).hexdigest()


async def save_upload(state, content, name, create_project=False):
    upload_id = str(uuid.uuid4())
    directory = state["store"].directory
    path = directory / "uploads" / (upload_id + ".png")
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
    source = state["store"].one("SELECT * FROM uploads WHERE id=?", (source_id,)) if mode == "edit" else None
    if mode == "edit" and not source:
        return failure("Upload a photo or choose an image from your library first.")
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
    if mask_id is not None:
        if mode != "edit" or not isinstance(mask_id, str):
            return failure("Select an editing photo and paint an area first.")
        mask = state["store"].one("SELECT * FROM masks WHERE id=? AND source_id=?", (mask_id, source_id))
        if not mask:
            return failure("This selection belongs to a different photo. Paint it again.")
        sizing = masked_size(source, json.loads(mask["bounds"]), payload.get("mask_feather", 8),
                             quality, aspect, QUALITIES, state["upscaler"])
    else:
        sizing = plan_size(quality, aspect, mode, source, payload.get("output_size", "preset"), QUALITIES,
                           state["upscaler"], payload.get("resize_mode", "auto"))
    model = QUALITIES[quality]["model"]
    recipe = {"version": 2, "mode": mode, "prompt": prompt.strip(), "quality": quality, "aspect": aspect,
              **sizing, "model": model, "model_source": state["models"][model],
              "steps": 4, "guidance": 1.0, "snofs": payload.get("snofs", True),
              "adapter": state["adapter"] if payload.get("snofs", True) else None, "runtime": state["runtime"],
              "source_id": source_id if mode == "edit" else None,
              "source": {k: source[k] for k in ("name", "width", "height", "sha256")} if source else None,
              "parent_image_id": parent_id if mode == "edit" else None}
    if mask:
        recipe["masked_edit"] = {"mask_id": mask_id, "sha256": mask["sha256"],
                                  "bounds": json.loads(mask["bounds"]), "crop": sizing["processing"]["crop"],
                                  "feather": payload.get("mask_feather", 8), "context_padding": 64,
                                  "method": "crop_edit_blend", "preserve_unselected": True}
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
        content = (state["store"].directory / "images" / (version["id"]+".png")).read_bytes()
        result = await save_upload(state,content,"Studio image")
        state["store"].projects.bind_source(result["id"],version["project_id"],version["id"])
        result["parent_image_id"] = version["id"]
    else:
        upload = state["store"].one("SELECT * FROM uploads WHERE id=?", (version["id"],))
        result = {"id":upload["id"],"name":upload["name"],"width":upload["width"],"height":upload["height"],
                  "url":version["url"],"thumbnail":version["thumbnail"]}
    result.update(project_id=version["project_id"],parent_version_id=version["id"])
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
                    "SELECT id FROM project_sources WHERE project_id=? AND parent_id=?", (project["id"], version["id"])):
                source = await save_upload(state, (store.directory / "images" / (version["id"] + ".png")).read_bytes(), "Studio image")
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
              state["store"].directory / ("uploads" if v["kind"] == "upload" else "images") / (v["id"] + ".png")) for v in versions]
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
        size = masked_size(source,payload["mask_bounds"],payload.get("mask_feather",8),quality,aspect,QUALITIES,state["upscaler"])
    else:
        size = plan_size(quality,aspect,mode,source,payload.get("output_size","preset"),QUALITIES,state["upscaler"],payload.get("resize_mode","auto"))
    recipe = dict(size,mode=mode,model=QUALITIES[quality]["model"],snofs=payload.get("snofs",True))
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
    """Encode a sharing copy without changing the saved PNG or its dimensions."""
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
    folders = {"image": ("images", ".png"), "upload": ("uploads", ".png"), "thumb": ("thumbs", ".webp"), "mask": ("masks", ".png")}
    if kind not in folders:
        raise web.HTTPNotFound()
    image_exists = state["store"].one("SELECT id FROM images WHERE id=?", (media_id,))
    upload_exists = state["store"].one("SELECT id FROM uploads WHERE id=?", (media_id,))
    mask_exists = state["store"].one("SELECT id FROM masks WHERE id=?", (media_id,)) if kind == "mask" else None
    if not {"image": image_exists, "upload": upload_exists, "thumb": image_exists or upload_exists, "mask": mask_exists}[kind]:
        raise web.HTTPNotFound()
    folder, suffix = folders[kind]
    path = state["store"].directory / folder / (media_id + suffix)
    if not path.is_file():
        raise web.HTTPNotFound()
    output_format = request.query.get("format")
    if output_format is not None and (kind not in ("image", "upload") or output_format not in ("png", "jpg", "jpeg")):
        return failure("Choose PNG or JPG for a full-size image download.")
    headers = {}
    jpeg = output_format in ("jpg", "jpeg")
    if request.query.get("download") == "1":
        extension = ".jpg" if jpeg else suffix
        headers["Content-Disposition"] = f'attachment; filename="studio-{media_id}{extension}"'
    if jpeg:
        try:
            content = await asyncio.to_thread(jpeg_download, path)
        except FileNotFoundError:
            raise web.HTTPNotFound()
        return web.Response(body=content, content_type="image/jpeg", headers=headers)
    return web.FileResponse(path, headers=headers)


def create_app(data_dir=None, engine_url=None, project_root=None, pin=None, start_worker=True):
    directory = Path(data_dir or os.environ.get("STUDIO_DATA", ROOT / "studio-data"))
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
    app[STATE] = {"store": store, "engine": engine, "pin": pin, "attempts": {},
                  "models": json.loads((ROOT / "config/models.json").read_text()),
                  "adapter": json.loads((ROOT / "config/loras.json").read_text())["snofs-klein-9b-v1.4"],
                  "upscaler": json.loads((ROOT / "config/upscaler.json").read_text()),
                  "runtime": json.loads((ROOT / "config/runtime.json").read_text())}
    app[STATE]["metrics"] = Metrics(store,engine)
    app[STATE]["downloads"] = Downloads()
    app.cleanup_ctx.append(app[STATE]["downloads"].context)
    if start_worker:
        app.cleanup_ctx.append(engine.context)
    async def close_store(app):
        store.close()
    app.on_cleanup.append(close_store)
    app.add_routes([
        web.get("/", index), web.get("/static/{name}", static_file),
        web.get("/api/auth", auth), web.post("/api/login", login), web.post("/api/logout", logout),
        web.get("/api/health", health), web.get("/api/options", options),
        web.get("/api/favorites", favorites), web.post("/api/bulk", bulk_action),
        web.post("/api/bulk-download", bulk_download), web.get("/api/downloads/{token}", download_archive),
        web.get("/api/projects",project_library), web.get("/api/projects/{id}",project_detail),
        web.patch("/api/projects/{id}",update_project), web.delete("/api/projects/{id}",delete_project),
        web.post("/api/projects/{id}/versions/{version_id}/edit-source",project_edit_source),
        web.patch("/api/projects/{id}/versions/{version_id}",update_version),
        web.delete("/api/projects/{id}/versions/{version_id}",delete_version),
        web.post("/api/estimate",estimate), web.get("/api/metrics",metrics),
        web.post("/api/uploads/{id}/mask",upload_mask), web.get("/api/masks/{id}",mask_detail),
        web.post("/api/uploads", upload), web.post("/api/generate", generate),
        web.get("/api/jobs", jobs), web.post("/api/jobs/{id}/cancel", cancel),
        web.get("/api/images", library), web.get("/api/images/{id}", image_detail),
        web.delete("/api/images/{id}", delete_image),
        web.patch("/api/images/{id}", update_image), web.post("/api/images/{id}/edit-source", edit_source),
        web.get("/api/images/{id}/recipe", recipe_file), web.get("/media/{kind}/{id}", media),
    ])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("STUDIO_PORT", "7878")))
    args = parser.parse_args()
    app = create_app()
    print(f"Studio PIN: {app[STATE]['pin']} (saved in {app[STATE]['store'].directory / 'access-pin.txt'})", flush=True)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

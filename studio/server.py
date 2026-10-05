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
from .sizing import OUTPUT_SIZES, MAX_PIXELS, plan_size

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
    if name not in ("app.js", "style.css", "icon.svg", "manifest.json"):
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
    return web.json_response({"qualities": QUALITIES, "output_sizes": OUTPUT_SIZES, "engine_ready": await state["engine"].available(),
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


async def save_upload(state, content, name):
    upload_id = str(uuid.uuid4())
    directory = state["store"].directory
    path = directory / "uploads" / (upload_id + ".png")
    try:
        width, height, digest = await asyncio.to_thread(normalized_upload, content, path, directory / "thumbs" / (upload_id + ".webp"))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        path.unlink(missing_ok=True)
        raise ValueError("Choose a valid PNG, JPEG or WebP photo under 20 MB and 16 megapixels.")
    state["store"].execute("INSERT INTO uploads VALUES (?,?,?,?,?,?)", (upload_id, time.time(), name[:150], width, height, digest))
    return {"id": upload_id, "name": name[:150], "width": width, "height": height, "url": f"/media/upload/{upload_id}", "thumbnail": f"/media/thumb/{upload_id}"}


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
    return web.json_response(await save_upload(request.app[STATE], bytes(chunks), part.filename or "Photo"), status=201)


async def edit_source(request):
    state = request.app[STATE]
    image = state["store"].image(request.match_info["id"])
    if not image:
        raise web.HTTPNotFound()
    data = (state["store"].directory / "images" / image["filename"]).read_bytes()
    result = await save_upload(state, data, "Studio image")
    result["parent_image_id"] = image["id"]
    return web.json_response(result, status=201)


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
    parent_id = payload.get("parent_image_id")
    if parent_id and not state["store"].image(parent_id):
        # The saved editing source is independent of the parent image, so a
        # reused recipe still works after that earlier result is deleted.
        parent_id = None
    pending = state["store"].one("SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','submitted','running')")["count"]
    if pending + count > 24:
        return failure("Your queue is full. Wait for some images to finish before adding more.", 409)
    sizing = plan_size(quality, aspect, mode, source, payload.get("output_size", "preset"), QUALITIES, state["upscaler"])
    model = QUALITIES[quality]["model"]
    recipe = {"version": 2, "mode": mode, "prompt": prompt.strip(), "quality": quality, "aspect": aspect,
              **sizing, "model": model, "model_source": state["models"][model],
              "steps": 4, "guidance": 1.0, "snofs": payload.get("snofs", True),
              "adapter": state["adapter"] if payload.get("snofs", True) else None, "runtime": state["runtime"],
              "source_id": source_id if mode == "edit" else None,
              "source": {k: source[k] for k in ("name", "width", "height", "sha256")} if source else None,
              "parent_image_id": parent_id if mode == "edit" else None}
    base_seed = seed if seed is not None else secrets.randbits(32)
    recipes = [dict(recipe, seed=(base_seed + i) % 2**32) for i in range(count)]
    result = state["store"].add_jobs(recipes)
    state["engine"].wake.set()
    return web.json_response(result, status=202)


async def jobs(request):
    store = request.app[STATE]["store"]
    rows = store.rows("SELECT id FROM jobs WHERE status IN ('queued','submitted','running') OR created>? ORDER BY created DESC,variation LIMIT 100", (time.time() - 86400,))
    return web.json_response({"jobs": [store.job(row["id"]) for row in rows]})


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
    store.execute(f"UPDATE images SET {','.join(k+'=?' for k in payload)} WHERE id=?", (*map(int, payload.values()), image_id))
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


async def media(request):
    state = request.app[STATE]
    media_id = request.match_info["id"]
    try:
        uuid.UUID(media_id)
    except ValueError:
        raise web.HTTPNotFound()
    kind = request.match_info["kind"]
    folders = {"image": ("images", ".png"), "upload": ("uploads", ".png"), "thumb": ("thumbs", ".webp")}
    if kind not in folders:
        raise web.HTTPNotFound()
    image_exists = state["store"].one("SELECT id FROM images WHERE id=?", (media_id,))
    upload_exists = state["store"].one("SELECT id FROM uploads WHERE id=?", (media_id,))
    if not {"image": image_exists, "upload": upload_exists, "thumb": image_exists or upload_exists}[kind]:
        raise web.HTTPNotFound()
    folder, suffix = folders[kind]
    path = state["store"].directory / folder / (media_id + suffix)
    if not path.is_file():
        raise web.HTTPNotFound()
    headers = {}
    if request.query.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="studio-{media_id}{suffix}"'
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
    if start_worker:
        app.cleanup_ctx.append(engine.context)
    async def close_store(app):
        store.close()
    app.on_cleanup.append(close_store)
    app.add_routes([
        web.get("/", index), web.get("/static/{name}", static_file),
        web.get("/api/auth", auth), web.post("/api/login", login), web.post("/api/logout", logout),
        web.get("/api/health", health), web.get("/api/options", options),
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

import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, PngImagePlugin

from studio.server import STATE, create_app


def photo(width=128, height=128):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "navy").save(stream, "PNG")
    return stream.getvalue()


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, pin="123456", start_worker=False)
        self.store = self.app[STATE]["store"]
        self.client = TestClient(TestServer(self.app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def login(self):
        response = await self.client.post("/api/login", json={"pin": "123456"})
        self.assertEqual(response.status, 200)

    async def seed_image(self):
        await self.login()
        response = await self.client.post("/api/generate", json={"prompt": "A fox in snow", "seed": 12})
        image_id = (await response.json())["jobs"][0]
        recipe = self.store.job(image_id)["recipe"]
        path = self.store.directory / "images" / (image_id + ".png")
        path.write_bytes(photo())
        self.store.execute("INSERT INTO images (id,created,filename,recipe) VALUES (?,?,?,?)", (image_id, time.time(), path.name, json.dumps(recipe)))
        self.store.update_job(image_id, status="complete")
        return image_id, recipe

    async def test_pin_protects_images_and_mutations_and_rejects_cross_origin(self):
        for path in ("/api/images", "/api/jobs", "/api/options", "/media/image/anything"):
            self.assertEqual((await self.client.get(path)).status, 401)
        self.assertEqual((await self.client.post("/api/generate", json={"prompt": "test"})).status, 401)
        await self.login()
        response = await self.client.post("/api/generate", json={"prompt": "test"}, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status, 403)
        await self.client.post("/api/logout", json={})
        self.assertEqual((await self.client.get("/api/images")).status, 401)

    async def test_pin_attempts_are_limited(self):
        for _ in range(8):
            self.assertEqual((await self.client.post("/api/login", json={"pin": "000000"})).status, 401)
        self.assertEqual((await self.client.post("/api/login", json={"pin": "123456"})).status, 429)

    async def test_variations_keep_distinct_seeds_and_quality(self):
        await self.login()
        response = await self.client.post("/api/generate", json={"prompt": "A cafe", "variations": 4, "seed": 4294967294, "quality": "fast", "aspect": "portrait"})
        self.assertEqual(response.status, 202)
        result = await response.json()
        jobs = [self.store.job(job_id) for job_id in result["jobs"]]
        self.assertEqual([j["recipe"]["seed"] for j in jobs], [4294967294, 4294967295, 0, 1])
        self.assertEqual({j["recipe"]["model"] for j in jobs}, {"flux2-klein-9b-4bit"})
        self.assertTrue(all(j["recipe"]["width"] == 512 and j["recipe"]["height"] == 640 for j in jobs))
        self.assertEqual([j["variation"] for j in jobs], [1, 2, 3, 4])
        for change in ({"variations": 9}, {"seed": True}, {"quality": "unknown"}, {"mode": "edit"}, {"snofs": "true"}):
            response = await self.client.post("/api/generate", json={"prompt": "test", **change})
            self.assertEqual(response.status, 400, change)

    async def test_archive_favorite_recipe_and_session_survive_restart(self):
        image_id, recipe = await self.seed_image()
        await self.client.patch(f"/api/images/{image_id}", json={"favorite": True})
        self.assertEqual((await (await self.client.get("/api/images?section=favorites")).json())["total"], 1)
        await self.client.patch(f"/api/images/{image_id}", json={"archived": True})
        self.assertEqual((await (await self.client.get("/api/images")).json())["total"], 0)
        self.assertEqual((await (await self.client.get("/api/images?section=archive")).json())["total"], 1)
        cookie = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))["studio_session"].value
        await self.client.close()
        self.app = create_app(self.temp.name, pin="123456", start_worker=False)
        self.store = self.app[STATE]["store"]
        self.client = TestClient(TestServer(self.app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        self.client.session.cookie_jar.update_cookies({"studio_session": cookie}, response_url=self.client.make_url("/"))
        response = await self.client.get(f"/api/images/{image_id}/recipe")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), recipe)
        self.assertTrue(self.store.image(image_id)["favorite"])
        self.assertTrue(self.store.image(image_id)["archived"])
        await self.client.patch(f"/api/images/{image_id}", json={"archived": False})
        self.assertEqual((await (await self.client.get("/api/images?section=favorites")).json())["total"], 1)

    async def test_upload_edit_lineage_and_queued_cancellation(self):
        image_id, _ = await self.seed_image()
        response = await self.client.post(f"/api/images/{image_id}/edit-source", json={})
        source = await response.json()
        self.assertEqual(source["parent_image_id"], image_id)
        self.assertEqual((await self.client.get(source["url"])).status, 200)
        response = await self.client.post("/api/generate", json={"prompt": "Change the coat to red", "mode": "edit", "source_id": source["id"], "parent_image_id": image_id})
        job_id = (await response.json())["jobs"][0]
        job = self.store.job(job_id)
        self.assertEqual(job["recipe"]["parent_image_id"], image_id)
        self.assertEqual(job["recipe"]["source"]["sha256"], self.store.one("SELECT sha256 FROM uploads WHERE id=?", (source["id"],))["sha256"])
        await self.client.post(f"/api/jobs/{job_id}/cancel", json={})
        self.assertEqual(self.store.job(job_id)["status"], "cancelled")
        self.assertTrue((self.store.directory / "images" / (image_id + ".png")).is_file())
        form = aiohttp.FormData(); form.add_field("image", b"not a picture", filename="bad.png", content_type="image/png")
        self.assertEqual((await self.client.post("/api/uploads", data=form)).status, 400)

    async def test_search_uses_prompt_and_preserves_recipe(self):
        image_id, recipe = await self.seed_image()
        self.assertEqual((await (await self.client.get("/api/images?q=FOX")).json())["images"][0]["id"], image_id)
        self.assertEqual((await (await self.client.get("/api/images?q=unrelated")).json())["total"], 0)
        self.assertEqual((await (await self.client.get(f"/api/images/{image_id}/recipe")).json()), recipe)

    async def test_jpg_download_keeps_full_size_png_recipe_and_requires_login(self):
        image_id, recipe = await self.seed_image()
        path = self.store.directory / "images" / (image_id + ".png")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("studio", json.dumps(recipe))
        metadata.add_text("workflow", '{"nodes": []}')
        Image.new("RGB", (751,497), (30,80,170)).save(path, "PNG", pnginfo=metadata)
        original = path.read_bytes()
        files = set(path.parent.iterdir())
        url = f"/media/image/{image_id}?download=1&format=jpg"
        response = await self.client.get(url)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertEqual(response.headers["Content-Disposition"], f'attachment; filename="studio-{image_id}.jpg"')
        with Image.open(io.BytesIO(await response.read())) as jpg:
            self.assertEqual(jpg.format, "JPEG")
            self.assertEqual(jpg.mode, "RGB")
            self.assertEqual(jpg.size, (751,497))
            self.assertTrue(all(abs(a-b) <= 2 for a,b in zip(jpg.getpixel((300,200)),(30,80,170))))
        png = await self.client.get(f"/media/image/{image_id}?download=1")
        self.assertEqual(png.content_type, "image/png")
        self.assertEqual(await png.read(), original)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(set(path.parent.iterdir()), files)
        self.assertEqual(self.store.image(image_id)["recipe"], recipe)
        self.assertEqual((await self.client.get(f"/media/image/{image_id}?format=gif")).status,400)
        self.assertEqual((await self.client.get("/media/image/00000000-0000-0000-0000-000000000000?format=jpg")).status,404)
        await self.client.post("/api/logout",json={})
        self.assertEqual((await self.client.get(url)).status,401)

    async def test_jpg_export_flattens_transparency_and_supports_saved_sources(self):
        image_id, _ = await self.seed_image()
        path = self.store.directory / "images" / (image_id + ".png")
        Image.new("RGBA", (79,65), (255,0,0,0)).save(path,"PNG")
        response = await self.client.get(f"/media/image/{image_id}?format=jpeg&download=1")
        self.assertEqual(response.status,200)
        with Image.open(io.BytesIO(await response.read())) as jpg:
            self.assertEqual(jpg.size,(79,65))
            self.assertEqual(jpg.getpixel((30,30)),(255,255,255))
        with Image.open(path) as png:
            self.assertEqual(png.mode,"RGBA")
            self.assertEqual(png.getpixel((30,30)),(255,0,0,0))
        form = aiohttp.FormData()
        form.add_field("image",photo(643,427),filename="source.png",content_type="image/png")
        source = await (await self.client.post("/api/uploads",data=form)).json()
        response = await self.client.get(source["url"] + "?download=1&format=jpg")
        self.assertEqual(response.content_type,"image/jpeg")
        with Image.open(io.BytesIO(await response.read())) as jpg:
            self.assertEqual(jpg.size,(643,427))
        self.assertEqual((await self.client.get(source["thumbnail"] + "?format=jpg")).status,400)

    async def test_delete_requires_confirmation_and_removes_files_recipe_and_job(self):
        image_id, _ = await self.seed_image()
        kept_id, kept_recipe = await self.seed_image()
        thumbnail = self.store.directory / "thumbs" / (image_id + ".webp")
        Image.new("RGB", (128,128), "navy").save(thumbnail, "WEBP")
        path = self.store.directory / "images" / (image_id + ".png")
        await self.client.patch(f"/api/images/{image_id}", json={"favorite":True,"archived":True})
        await self.client.post("/api/logout", json={})
        self.assertEqual((await self.client.delete(f"/api/images/{image_id}", json={"confirm":True})).status,401)
        await self.login()
        self.assertEqual((await self.client.delete(f"/api/images/{image_id}", json={"confirm":True}, headers={"Origin":"https://unrelated.example"})).status,403)
        for payload in ({}, {"confirm":False}, {"confirm":1}, {"confirm":"true"}):
            self.assertEqual((await self.client.delete(f"/api/images/{image_id}", json=payload)).status,400)
            self.assertTrue(path.is_file())
            self.assertTrue(thumbnail.is_file())
        self.store.update_job(image_id, status="running")
        self.assertEqual((await self.client.delete(f"/api/images/{image_id}", json={"confirm":True})).status,409)
        self.store.update_job(image_id, status="complete")
        response = await self.client.delete(f"/api/images/{image_id}", json={"confirm":True})
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())["deleted_id"],image_id)
        self.assertFalse(path.exists())
        self.assertFalse(thumbnail.exists())
        self.assertIsNone(self.store.image(image_id))
        self.assertIsNone(self.store.job(image_id))
        for route in (f"/api/images/{image_id}",f"/api/images/{image_id}/recipe",f"/media/image/{image_id}",f"/media/thumb/{image_id}"):
            self.assertEqual((await self.client.get(route)).status,404)
        library = await (await self.client.get("/api/images")).json()
        self.assertEqual(library["counts"],{"total":1,"library":1,"favorites":0,"archive":0})
        self.assertEqual(self.store.image(kept_id)["recipe"],kept_recipe)
        self.assertEqual((await self.client.delete(f"/api/images/{image_id}",json={"confirm":True})).status,404)

    async def test_deleting_parent_preserves_edit_sources_and_reusable_recipes(self):
        parent_id, _ = await self.seed_image()
        source = await (await self.client.post(f"/api/images/{parent_id}/edit-source",json={})).json()
        content = await (await self.client.get(source["url"])).read()
        response = await self.client.post("/api/generate",json={"mode":"edit","prompt":"Add flowers","source_id":source["id"],"parent_image_id":parent_id})
        child_id = (await response.json())["jobs"][0]
        recipe = self.store.job(child_id)["recipe"]
        child = self.store.directory / "images" / (child_id + ".png")
        child.write_bytes(photo())
        self.store.execute("INSERT INTO images (id,created,filename,recipe) VALUES (?,?,?,?)",(child_id,time.time(),child.name,json.dumps(recipe)))
        self.store.update_job(child_id,status="complete")
        response = await self.client.delete(f"/api/images/{parent_id}",json={"confirm":True})
        self.assertEqual(response.status,200)
        self.assertTrue(child.is_file())
        self.assertEqual(self.store.image(child_id)["recipe"],recipe)
        self.assertEqual(await (await self.client.get(source["url"])).read(),content)
        self.assertEqual((await self.client.get(source["thumbnail"])).status,200)
        response = await self.client.post("/api/generate",json=recipe)
        self.assertEqual(response.status,202)
        reused = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual(reused["source_id"],source["id"])
        self.assertEqual(reused["source"]["sha256"],recipe["source"]["sha256"])
        self.assertIsNone(reused["parent_image_id"])

    async def test_restart_recovers_interrupted_deletions_without_restoring_deleted_images(self):
        kept_id, _ = await self.seed_image()
        deleted_id, _ = await self.seed_image()
        for image_id in (kept_id,deleted_id):
            for folder,suffix in (("images",".png"),("thumbs",".webp")):
                path = self.store.directory / folder / (image_id + suffix)
                if folder == "thumbs": Image.new("RGB",(128,128),"navy").save(path,"WEBP")
                path.rename(path.with_name(path.name+".deleting"))
        with self.store.db:
            self.store.db.execute("DELETE FROM images WHERE id=?",(deleted_id,))
            self.store.db.execute("DELETE FROM jobs WHERE id=?",(deleted_id,))
        await self.client.close()
        self.app = create_app(self.temp.name,pin="123456",start_worker=False)
        self.store = self.app[STATE]["store"]
        self.client = TestClient(TestServer(self.app),cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        await self.login()
        for folder,suffix in (("images",".png"),("thumbs",".webp")):
            self.assertTrue((self.store.directory/folder/(kept_id+suffix)).is_file())
            self.assertFalse((self.store.directory/folder/(deleted_id+suffix)).exists())
            self.assertFalse(list((self.store.directory/folder).glob("*.deleting")))
        self.assertEqual((await (await self.client.get("/api/images")).json())["total"],1)
        self.assertIsNone(self.store.job(deleted_id))

    async def test_output_sizes_preserve_source_shape_and_exact_dimensions(self):
        await self.login()
        form = aiohttp.FormData()
        form.add_field("image", photo(751,497), filename="reference.png", content_type="image/png")
        source = await (await self.client.post("/api/uploads", data=form)).json()
        for output_size, expected in (("original",(751,497)),("2k",(2048,1355)),("4k",(4096,2711))):
            response = await self.client.post("/api/generate",json={"mode":"edit","prompt":"Add flowers","quality":"fast","aspect":"portrait","source_id":source["id"],"output_size":output_size})
            self.assertEqual(response.status,202)
            recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
            self.assertEqual((recipe["width"],recipe["height"]),expected)
            self.assertEqual(recipe["output_size"],output_size)
            self.assertTrue(all(recipe[k] % 16 == 0 and recipe[k] <= 2048 for k in ("generation_width","generation_height")))
            self.assertLessEqual(recipe["generation_width"]*recipe["generation_height"],1_100_000)
            self.assertEqual(recipe["processing"]["upscaler"]["scale"],4)
            self.assertEqual(recipe["processing"]["crop"],"disabled")
        for change in ({"output_size":"original"},{"output_size":"unknown"},{"output_size":4}):
            response = await self.client.post("/api/generate",json={"prompt":"A cafe",**change})
            self.assertEqual(response.status,400)
        response = await self.client.post("/api/generate",json={"prompt":"A cafe","output_size":"4k","aspect":"square","quality":"fast"})
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual((recipe["width"],recipe["height"]),(4096,4096))
        self.assertEqual((recipe["generation_width"],recipe["generation_height"]),(1024,1024))


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.submitted = []
        self.history = {}
        self.files = {}
        self.sockets = []
        self.upload_names = []
        self.held = set()
        self.hold_next = False
        self.cancelled = []
        fake = web.Application()
        async def stats(request): return web.json_response({"system": {"argv": []}})
        async def queue(request): return web.json_response({"queue_running": [[0, prompt_id] for prompt_id in self.held], "queue_pending": []})
        async def cancel(request):
            prompt_id = request.match_info["id"]
            self.cancelled.append(prompt_id)
            self.held.discard(prompt_id)
            return web.json_response({"ok": True})
        async def upload(request):
            reader = await request.multipart()
            while part := await reader.next():
                if part.name == "image":
                    self.upload_names.append(part.filename)
                    await part.read()
            return web.json_response({"name": self.upload_names[-1], "subfolder": "web-studio"})
        async def websocket(request):
            ws = web.WebSocketResponse(); await ws.prepare(request); self.sockets.append(ws)
            async for _ in ws: pass
            return ws
        async def submit(request):
            data = await request.json(); self.submitted.append(data)
            prompt_id = data["prompt_id"]; recipe = data["extra_data"]["extra_pnginfo"]["studio"]
            metadata = PngImagePlugin.PngInfo()
            for key,value in data["extra_data"]["extra_pnginfo"].items(): metadata.add_text(key,json.dumps(value))
            metadata.add_text("prompt",json.dumps(data["prompt"]))
            inputs = data["prompt"]["2"]["inputs"]
            stream = io.BytesIO(); Image.new("RGB",(inputs["width"],inputs["height"]),"navy").save(stream,"PNG",pnginfo=metadata)
            self.files[prompt_id] = stream.getvalue()
            self.history[prompt_id] = {"outputs": {"2": {"images": [{"filename": prompt_id + ".png", "subfolder": "", "type": "output"}]}}, "status": {"status_str": "success", "messages": []}}
            if "9" in data["prompt"]:
                stream = io.BytesIO(); Image.new("RGB",(recipe["width"],recipe["height"]),"navy").save(stream,"PNG",pnginfo=metadata)
                self.files[prompt_id + "-final"] = stream.getvalue()
                self.history[prompt_id]["outputs"]["9"] = {"images": [{"filename": prompt_id + "-final.png","subfolder":"","type":"output"}]}
            if self.hold_next:
                self.hold_next = False
                self.held.add(prompt_id)
                self.history.pop(prompt_id)
            for ws in self.sockets:
                if not ws.closed: await ws.send_json({"type": "progress", "data": {"prompt_id": prompt_id, "value": 4, "max": 4}})
            return web.json_response({"prompt_id": prompt_id})
        async def history(request):
            prompt_id = request.match_info["id"]
            return web.json_response({prompt_id:self.history[prompt_id]} if prompt_id in self.history else {})
        async def view(request): return web.Response(body=self.files[request.query["filename"].removesuffix(".png")],content_type="image/png")
        fake.add_routes([web.get("/system_stats",stats),web.get("/queue",queue),web.post("/api/jobs/{id}/cancel",cancel),web.post("/upload/image",upload),web.get("/ws",websocket),web.post("/prompt",submit),web.get("/history/{id}",history),web.get("/view",view)])
        self.fake_server = TestServer(fake); await self.fake_server.start_server()
        self.app = create_app(self.temp.name, engine_url=str(self.fake_server.make_url("/")).rstrip("/"), project_root="/test/project",pin="123456")
        self.client = TestClient(TestServer(self.app),cookie_jar=aiohttp.CookieJar(unsafe=True)); await self.client.start_server()
        await self.client.post("/api/login",json={"pin":"123456"})

    async def asyncTearDown(self):
        await self.client.close(); await self.fake_server.close(); self.temp.cleanup()

    async def wait_images(self,count):
        for _ in range(100):
            result = await (await self.client.get("/api/images")).json()
            if result["total"] == count: return result["images"]
            await asyncio.sleep(.05)
        self.fail("Image worker did not finish")

    async def wait_submitted(self, count):
        for _ in range(100):
            if len(self.submitted) == count:
                return
            await asyncio.sleep(.05)
        self.fail("Image worker did not submit the job")

    async def test_cancel_targets_only_its_prompt_and_next_variation_finishes(self):
        self.hold_next = True
        response = await self.client.post("/api/generate", json={"prompt": "A cafe", "quality": "fast", "variations": 2})
        ids = (await response.json())["jobs"]
        await self.wait_submitted(1)
        await self.client.post(f"/api/jobs/{ids[0]}/cancel", json={})
        images = await self.wait_images(1)
        self.assertEqual(images[0]["id"], ids[1])
        self.assertEqual(self.cancelled, [self.submitted[0]["prompt_id"]])
        self.assertEqual(self.app[STATE]["store"].job(ids[0])["status"], "cancelled")

    async def test_restart_recovers_existing_engine_job_without_resubmitting(self):
        self.hold_next = True
        await self.client.post("/api/generate", json={"prompt": "A cafe", "quality": "fast"})
        await self.wait_submitted(1)
        prompt_id = self.submitted[0]["prompt_id"]
        await self.client.close()
        self.assertFalse(self.cancelled)
        self.history[prompt_id] = {"outputs": {"2": {"images": [{"filename": prompt_id + ".png", "subfolder": "", "type": "output"}]}}, "status": {"status_str": "success", "messages": []}}
        self.held.clear()
        self.app = create_app(self.temp.name, engine_url=str(self.fake_server.make_url("/")).rstrip("/"), project_root="/test/project", pin="123456")
        self.client = TestClient(TestServer(self.app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.post("/api/login", json={"pin": "123456"})
        await self.wait_images(1)
        self.assertEqual(len(self.submitted), 1)

    async def test_worker_generates_variations_then_edits_and_keeps_png_recipe(self):
        response = await self.client.post("/api/generate",json={"prompt":"An Italian cafe","quality":"fast","variations":2,"seed":123})
        self.assertEqual(response.status,202)
        images = await self.wait_images(2)
        self.assertEqual({image["recipe"]["seed"] for image in images},{123,124})
        self.assertEqual(len(self.submitted),2)
        self.assertTrue(all(x["prompt"]["1"]["inputs"]["custom_model_path"] == "/test/project/models/flux2-klein-9b-4bit" for x in self.submitted))
        source = await (await self.client.post(f"/api/images/{images[0]['id']}/edit-source",json={})).json()
        await self.client.post("/api/generate",json={"mode":"edit","prompt":"Add red flowers","quality":"fast","source_id":source["id"],"parent_image_id":images[0]["id"]})
        all_images = await self.wait_images(3)
        edited = next(image for image in all_images if image["recipe"]["mode"] == "edit")
        graph = self.submitted[-1]["prompt"]
        self.assertEqual(graph["2"]["inputs"]["image"],["5",0])
        self.assertFalse(graph["2"]["inputs"]["use_kv_cache"])
        self.assertEqual(len(self.upload_names),1)
        path = Path(self.temp.name)/"images"/(edited["id"]+".png")
        with Image.open(path) as image:
            self.assertEqual(json.loads(image.info["studio"]),edited["recipe"])
            self.assertIn("workflow",image.info)
        self.assertEqual((await self.client.get(f"/media/thumb/{edited['id']}")).status,200)

    async def test_worker_saves_final_size_png_with_complete_upscale_workflow(self):
        form = aiohttp.FormData()
        form.add_field("image",photo(643,427),filename="reference.png",content_type="image/png")
        source = await (await self.client.post("/api/uploads",data=form)).json()
        await self.client.post("/api/generate",json={"mode":"edit","prompt":"Add red flowers","source_id":source["id"],"output_size":"original","quality":"fast"})
        image = (await self.wait_images(1))[0]
        self.assertEqual((image["recipe"]["width"],image["recipe"]["height"]),(643,427))
        submitted = self.submitted[-1]
        graph = submitted["prompt"]
        self.assertEqual(graph["6"]["class_type"],"UpscaleModelLoader")
        self.assertEqual(graph["7"]["inputs"]["image"],["2",0])
        self.assertEqual(graph["8"]["inputs"]["width"],643)
        self.assertEqual(graph["9"]["inputs"]["images"],["8",0])
        path = Path(self.temp.name)/"images"/(image["id"]+".png")
        with Image.open(path) as png:
            self.assertEqual(png.size,(643,427))
            self.assertEqual(json.loads(png.info["studio"]),image["recipe"])
            workflow = json.loads(png.info["workflow"])
            self.assertTrue({"UpscaleModelLoader","ImageUpscaleWithModel","ImageScale","SaveImage"} <= {n["type"] for n in workflow["nodes"]})
            nodes = {node["id"]:node for node in workflow["nodes"]}
            for link_id,origin,origin_slot,target,target_slot,_ in workflow["links"]:
                self.assertIn(link_id,nodes[origin]["outputs"][origin_slot]["links"])
                self.assertEqual(nodes[target]["inputs"][target_slot]["link"],link_id)
        await self.client.post("/api/generate",json={"prompt":"A cafe","quality":"fast","output_size":"2k"})
        images = await self.wait_images(2)
        image_2k = next(item for item in images if item["recipe"]["output_size"] == "2k")
        with Image.open(Path(self.temp.name)/"images"/(image_2k["id"]+".png")) as png:
            self.assertEqual(png.size,(2048,2048))

        # A canvas rounded up to the next multiple of 16 needs only a resize.
        form = aiohttp.FormData()
        form.add_field("image",photo(79,79),filename="small.png",content_type="image/png")
        small = await (await self.client.post("/api/uploads",data=form)).json()
        response = await self.client.post("/api/generate",json={"mode":"edit","prompt":"Add flowers","source_id":small["id"],"output_size":"original","quality":"fast"})
        small_id = (await response.json())["jobs"][0]
        images = await self.wait_images(3)
        small_image = next(item for item in images if item["id"] == small_id)
        self.assertEqual(small_image["recipe"]["processing"]["method"],"resize")
        graph = self.submitted[-1]["prompt"]
        self.assertNotIn("6",graph)
        self.assertNotIn("7",graph)
        self.assertEqual(graph["8"]["inputs"]["image"],["2",0])
        with Image.open(Path(self.temp.name)/"images"/(small_id+".png")) as png:
            self.assertEqual(png.size,(79,79))
            self.assertEqual(json.loads(png.info["studio"]),small_image["recipe"])


if __name__ == "__main__":
    unittest.main()

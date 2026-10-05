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
            stream = io.BytesIO(); Image.new("RGB",(recipe["width"],recipe["height"]),"navy").save(stream,"PNG",pnginfo=metadata)
            self.files[prompt_id] = stream.getvalue()
            self.history[prompt_id] = {"outputs": {"2": {"images": [{"filename": prompt_id + ".png", "subfolder": "", "type": "output"}]}}, "status": {"status_str": "success", "messages": []}}
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


if __name__ == "__main__":
    unittest.main()

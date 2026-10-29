import asyncio
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, PngImagePlugin

from studio.engine import make_thumbnail
from studio.server import STATE, create_app

PARITY = Path(__file__).with_name("offline_parity.cjs")


class OfflineCopyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, pin="123456", start_worker=False)
        self.store = self.app[STATE]["store"]
        self.client = TestClient(TestServer(self.app), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.post("/api/login", json={"pin": "123456"})

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def upload(self, name):
        stream = io.BytesIO()
        Image.new("RGB", (240, 160), "navy").save(stream, "PNG")
        form = aiohttp.FormData()
        form.add_field("image", stream.getvalue(), filename=name, content_type="image/png")
        response = await self.client.post("/api/uploads", data=form)
        self.assertEqual(response.status, 201)
        return await response.json()

    def complete(self, image_id):
        recipe = self.store.job(image_id)["recipe"]
        path = self.store.directory / "images" / (image_id + ".png")
        info = PngImagePlugin.PngInfo()
        info.add_text("studio", json.dumps(recipe))
        Image.new("RGB", (recipe["width"], recipe["height"]), "coral").save(path, pnginfo=info)
        make_thumbnail(path, self.store.directory / "thumbs" / (image_id + ".webp"))
        self.store.execute("INSERT INTO images (id,created,filename,recipe) VALUES (?,?,?,?)", (image_id, time.time(), path.name, json.dumps(recipe)))
        self.store.update_job(image_id, status="complete", started=time.time() - 20, finished=time.time(), progress=1)

    async def generate(self, **payload):
        response = await self.client.post("/api/generate", json={"quality": "fast", "seed": 7, **payload})
        self.assertEqual(response.status, 202)
        jobs = (await response.json())["jobs"]
        for image_id in jobs:
            self.complete(image_id)
        return jobs

    async def edit(self, project_id, version_id, prompt, count=1):
        source = await (await self.client.post(f"/api/projects/{project_id}/versions/{version_id}/edit-source", json={})).json()
        return await self.generate(mode="edit", prompt=prompt, output_size="original", resize_mode="direct",
                                   source_id=source["id"], project_id=project_id, variations=count)

    async def json(self, method, path, body=None):
        response = await self.client.request(method, path, json=body)
        return {"status": response.status, "body": await response.json()}

    async def test_copy_needs_the_pin_includes_archive_and_videos_and_skips_unchanged_downloads(self):
        upload = await self.upload("Harbor.png")
        await self.client.patch(f"/api/projects/{upload['project_id']}", json={"archived": True})
        video_id = str(uuid.uuid4())
        self.store.execute("INSERT INTO videos (id,created,status,recipe) VALUES (?,?,?,?)", (video_id, time.time(), "complete", json.dumps({"prompt": "Waves"})))
        first = await self.client.get("/api/offline")
        self.assertEqual(first.status, 200)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        copy = await first.json()
        self.assertEqual([(p["id"], p["archived"]) for p in copy["projects"]], [(upload["project_id"], True)])
        self.assertEqual(copy["videos"][0]["url"], f"/media/video/{video_id}")
        unchanged = await self.client.get("/api/offline", headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual((unchanged.status, await unchanged.read()), (304, b""))
        await self.client.patch(f"/api/projects/{upload['project_id']}/versions/{upload['id']}", json={"favorite": True})
        changed = await self.client.get("/api/offline", headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual(changed.status, 200)
        self.assertNotEqual(changed.headers["ETag"], first.headers["ETag"])
        async with aiohttp.ClientSession() as stranger:
            self.assertEqual((await stranger.get(self.client.make_url("/api/offline"))).status, 401)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    async def test_phone_copy_answers_like_the_mac_before_and_after_offline_changes(self):
        harbor = await self.upload("Harbor.png")
        first, second = await self.edit(harbor["project_id"], harbor["id"], "Add a flower", count=2)
        third, = await self.edit(harbor["project_id"], first, "Add a boat")
        garden = await self.upload("Garden.png")
        fox, = await self.generate(prompt="A red fox in the snow")
        lake, = await self.generate(prompt="A quiet lake at dawn")
        await self.client.patch(f"/api/projects/{harbor['project_id']}/versions/{first}", json={"favorite": True})
        await self.client.patch(f"/api/projects/{garden['project_id']}", json={"archived": True})
        # Clips made from the first edit and the fox belong to their projects.
        clips = []
        for project_id, version_id in ((harbor["project_id"], first), (fox, fox)):
            source = await (await self.client.post(f"/api/projects/{project_id}/versions/{version_id}/edit-source", json={})).json()
            clips.append(str(uuid.uuid4()))
            self.store.execute("INSERT INTO videos (id,created,status,recipe) VALUES (?,?,?,?)", (clips[-1], time.time(), "complete", json.dumps(
                {"prompt": "Waves roll in", "source_id": source["id"], "width": 768, "height": 512, "frames": 97, "fps": 24})))
        copy = await (await self.client.get("/api/offline")).json()
        self.assertEqual({v["id"] for p in copy["projects"] for v in p["videos"]}, set(clips))

        def queries():
            return [{"kind": "library", "section": section, "q": query, "offset": offset, "limit": limit}
                    for section, query, offset, limit in (("library", "", 0, 48), ("favorites", "", 0, 48), ("archive", "", 0, 48),
                                                          ("library", "FLOWER", 0, 48), ("library", "", 1, 1))] + \
                   [{"kind": "favorites", "q": query, "offset": 0, "limit": 48} for query in ("", "boat")] + \
                   [{"kind": "photos", "section": section, "q": query, "offset": offset, "limit": limit}
                    for section, query, offset, limit in (("library", "", 0, 48), ("archive", "", 0, 48), ("library", "fox", 0, 48), ("library", "", 2, 2))] + \
                   [{"kind": "detail", "id": harbor["project_id"]}]
        changes = [("PATCH", f"/api/projects/{fox}", {"favorite": True}),
                   ("PATCH", f"/api/projects/{harbor['project_id']}/versions/{first}", {"favorite": False}),
                   ("PATCH", f"/api/projects/{garden['project_id']}", {"archived": False}),
                   ("DELETE", f"/api/projects/{harbor['project_id']}/versions/{first}", {"confirm": True}),
                   ("POST", "/api/bulk", {"scope": "images", "ids": [harbor["id"], third], "action": "favorite", "favorite": True}),
                   ("POST", "/api/bulk", {"scope": "projects", "ids": [lake], "action": "delete"}),
                   ("DELETE", f"/api/projects/{lake}", {"confirm": True}),
                   ("PATCH", f"/api/projects/{harbor['project_id']}/versions/{second}", {"archived": True}),
                   ("DELETE", f"/api/projects/{harbor['project_id']}/versions/{uuid.uuid4()}", {"confirm": True}),
                   ("DELETE", f"/api/videos/{clips[0]}", {}), ("DELETE", f"/api/videos/{clips[0]}", {"confirm": True}),
                   ("DELETE", f"/api/videos/{uuid.uuid4()}", {"confirm": True}), ("DELETE", f"/api/projects/{fox}", {"confirm": True})]
        steps = queries() + [{"change": {"method": m, "path": p, "body": b}} for m, p, b in changes] + queries()
        phone = json.loads((await asyncio.to_thread(subprocess.run, ["node", str(PARITY)], input=json.dumps({"copy": copy, "steps": steps}),
                                                    capture_output=True, text=True, check=True)).stdout)

        async def answer(step):
            if "change" in step:
                change = step["change"]
                return await self.json(change["method"], change["path"], change["body"])
            if step["kind"] == "library":
                return (await self.json("GET", f"/api/projects?section={step['section']}&q={step['q']}&offset={step['offset']}&limit={step['limit']}"))["body"]
            if step["kind"] == "favorites":
                return (await self.json("GET", f"/api/favorites?q={step['q']}&offset={step['offset']}&limit={step['limit']}"))["body"]
            if step["kind"] == "photos":
                return (await self.json("GET", f"/api/photos?section={step['section']}&q={step['q']}&offset={step['offset']}&limit={step['limit']}"))["body"]
            return (await self.json("GET", f"/api/projects/{step['id']}"))["body"]
        mac = [await answer(step) for step in steps]
        for index, (step, expected, actual) in enumerate(zip(steps, mac, phone)):
            if expected.get("status") == 404:
                # aiohttp words its own not-found replies; only the status has to match.
                expected, actual = expected["status"], actual["status"]
            with self.subTest(index=index, step=step):
                self.assertEqual(actual, expected)
        self.assertEqual(len(phone), len(steps))

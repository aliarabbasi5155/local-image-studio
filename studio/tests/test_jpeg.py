import io
import json
import time
import unittest

import aiohttp
from PIL import Image

from studio.engine import make_thumbnail, save_result
from studio.media import media_file
from studio.tests import test_projects as project_fixtures


def upload_form(image, kind="PNG", **options):
    stream = io.BytesIO()
    image.save(stream, kind, **options)
    form = aiohttp.FormData()
    form.add_field("image", stream.getvalue(), filename="photo." + kind.lower(), content_type="image/" + kind.lower())
    return form


class JpegStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = project_fixtures.ProjectTests.asyncSetUp
    asyncTearDown = project_fixtures.ProjectTests.asyncTearDown

    def finish(self, image_id):
        # The engine's own last step: its PNG becomes a JPEG carrying the recipe.
        recipe = self.store.job(image_id)["recipe"]
        path = self.store.directory / "images" / (image_id + ".jpg")
        Image.new("RGB", (recipe["width"], recipe["height"]), "coral").save(path.with_suffix(".part"), "PNG")
        save_result(path.with_suffix(".part"), path, recipe)
        make_thumbnail(path, self.store.directory / "thumbs" / (image_id + ".webp"))
        self.store.execute("INSERT INTO images (id,created,filename,recipe) VALUES (?,?,?,?)", (image_id, time.time(), path.name, json.dumps(recipe)))
        self.store.update_job(image_id, status="complete", started=time.time() - 20, finished=time.time(), progress=1)
        return path

    async def test_new_uploads_and_results_are_jpeg_and_shared_without_reencoding(self):
        upload = await (await self.client.post("/api/uploads", data=upload_form(Image.new("RGB", (300, 200), "navy")))).json()
        saved = media_file(self.store.directory, "uploads", upload["id"])
        self.assertEqual(saved.suffix, ".jpg")
        with Image.open(saved) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (300, 200)))
        response = await self.client.get(f"/media/upload/{upload['id']}?download=1&format=jpg")
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.headers["Content-Disposition"], f'attachment; filename="studio-{upload["id"]}.jpg"')
        self.assertEqual(await response.read(), saved.read_bytes())
        self.assertEqual((await self.client.get(f"/media/upload/{upload['id']}?format=png")).status, 400)

        source = await (await self.client.post(f"/api/projects/{upload['project_id']}/versions/{upload['id']}/edit-source", json={})).json()
        jobs = (await (await self.client.post("/api/generate", json={"mode": "edit", "prompt": "Add a boat", "quality": "fast", "source_id": source["id"],
                                                                       "project_id": upload["project_id"], "output_size": "original", "resize_mode": "direct"})).json())["jobs"]
        result = self.finish(jobs[0])
        with Image.open(result) as image:
            self.assertEqual(json.loads(image.info["comment"]), self.store.image(jobs[0])["recipe"])
        # Editing a result again starts from its exact bytes, so edit chains add no extra JPEG loss.
        again = await (await self.client.post(f"/api/projects/{upload['project_id']}/versions/{jobs[0]}/edit-source", json={})).json()
        self.assertEqual(media_file(self.store.directory, "uploads", again["id"]).read_bytes(), result.read_bytes())
        response = await self.client.delete(f"/api/projects/{upload['project_id']}", json={"confirm": True})
        self.assertEqual(response.status, 200)
        self.assertFalse(result.exists())
        self.assertFalse(saved.exists())

    async def test_phone_photos_lose_rotation_and_location_and_old_pngs_still_work(self):
        photo = Image.new("RGB", (300, 200), "seagreen")
        exif = photo.getexif()
        exif[0x0112] = 6  # Rotated: the stored photo must be upright.
        exif[0x8825] = {2: (52.0, 22.0, 0.0)}  # GPS latitude.
        upload = await (await self.client.post("/api/uploads", data=upload_form(photo, "JPEG", exif=exif))).json()
        with Image.open(media_file(self.store.directory, "uploads", upload["id"])) as image:
            self.assertEqual(image.size, (200, 300))
            self.assertFalse(dict(image.getexif()))
        # A library from before the switch keeps its PNG files.
        legacy = await (await self.client.post("/api/uploads", data=upload_form(Image.new("RGB", (120, 80), "plum")))).json()
        jpeg = media_file(self.store.directory, "uploads", legacy["id"])
        with Image.open(jpeg) as image:
            image.save(jpeg.with_suffix(".png"), "PNG")
        jpeg.unlink()
        response = await self.client.get(f"/media/upload/{legacy['id']}")
        self.assertEqual(response.headers["Content-Type"], "image/png")
        converted = await self.client.get(f"/media/upload/{legacy['id']}?download=1&format=jpg")
        self.assertEqual(converted.headers["Content-Disposition"], f'attachment; filename="studio-{legacy["id"]}.jpg"')
        with Image.open(io.BytesIO(await converted.read())) as image:
            self.assertEqual(image.format, "JPEG")

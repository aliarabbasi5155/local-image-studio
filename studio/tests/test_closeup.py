import io
import json
import unittest

import aiohttp
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from studio.closeup import close_up_size, merge_detail
from studio.engine import QUALITIES
from studio.media import media_file
from studio.tests import test_projects as project_fixtures


def scene(size=(600, 400)):
    """A photo-like test image: soft shapes, a face-like disc and fine stripes."""
    image = Image.new("RGB", size, (90, 120, 160))
    draw = ImageDraw.Draw(image)
    draw.ellipse((200, 100, 380, 300), fill=(225, 180, 150))
    draw.rectangle((40, 250, 180, 380), fill=(60, 140, 70))
    for x in range(0, size[0], 6):
        draw.line((x, 0, x, 60), fill=(200, 200, 210), width=2)
    return image.filter(ImageFilter.GaussianBlur(1.2))


def coarse(image, sigma):
    return np.asarray(image.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32)


class MergeTests(unittest.TestCase):
    def sharp(self):
        """Ground truth with real fine structure: stripes, edges and a checker pattern."""
        image = Image.new("RGB", (880, 960), (90, 120, 160))
        draw = ImageDraw.Draw(image)
        draw.ellipse((160, 200, 720, 820), fill=(225, 180, 150))
        for x in range(0, 880, 8):
            draw.line((x, 0, x, 150), fill=(230, 230, 235), width=3)
        for x in range(300, 580, 12):
            for y in range(400, 640, 12):
                if (x + y) // 12 % 2:
                    draw.rectangle((x, y, x + 5, y + 5), fill=(120, 70, 60))
        return image

    def test_close_up_recovers_real_detail_while_keeping_what_the_photo_resolves(self):
        truth, scale = self.sharp(), 4
        # What the photo resolves: the truth at a quarter of the size, enlarged back.
        source = truth.resize((220, 240), Image.Resampling.LANCZOS).resize(truth.size, Image.Resampling.LANCZOS)
        # The model draws the real detail, a few pixels off and a little warmer.
        generated = Image.fromarray(np.clip(np.asarray(truth, dtype=np.float32) * 1.06 + 8, 0, 255).astype(np.uint8))
        generated = generated.transform(generated.size, Image.Transform.AFFINE, (1, 0, 6, 0, 1, -4), Image.Resampling.BICUBIC)
        result, info = merge_detail(source, generated, scale)
        error = lambda image: float(np.abs(np.asarray(image, dtype=np.float32) - np.asarray(truth, dtype=np.float32))[40:-40, 40:-40].mean())
        self.assertLess(error(result), error(source) * .25)
        # Everything coarser than the photo's own resolution still matches the photo.
        self.assertLess(float(np.abs(coarse(result, scale * 1.5) - coarse(source, scale * 1.5))[40:-40, 40:-40].mean()), 2.5)
        self.assertTrue(info["alignment"]["applied"])

    def test_where_the_model_invents_content_the_photo_wins(self):
        source = scene().resize((1200, 800), Image.Resampling.LANCZOS)
        generated = source.copy()
        draw = ImageDraw.Draw(generated)
        for x in range(500, 800, 10):
            for y in range(300, 600, 10):
                draw.rectangle((x, y, x + 4, y + 4), fill=(250, 20, 20) if (x + y) // 10 % 2 else (20, 20, 250))
        result, _ = merge_detail(source, generated, 2)
        inside = (slice(330, 570), slice(530, 770))
        self.assertLess(float(np.abs(np.asarray(result, dtype=np.float32)[inside] - np.asarray(source, dtype=np.float32)[inside]).mean()), 4)

    def test_sizes_follow_the_framed_shape(self):
        sizes = QUALITIES["high"]["sizes"]
        width, height = close_up_size([0, 0, 200, 300], "standard", sizes)
        self.assertEqual((width % 16, height % 16), (0, 0))
        self.assertLessEqual(width * height, 1_048_576)
        self.assertAlmostEqual(width / height, 2 / 3, delta=.03)
        self.assertEqual(close_up_size([0, 0, 200, 300], "2k", sizes), (1365, 2048))
        self.assertEqual(max(close_up_size([0, 0, 300, 200], "4k", sizes)), 4096)


class CloseUpAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = project_fixtures.ProjectTests.asyncSetUp
    asyncTearDown = project_fixtures.ProjectTests.asyncTearDown

    async def test_framed_area_becomes_an_aligned_source_and_a_base_model_close_up(self):
        stream = io.BytesIO(); scene().save(stream, "PNG")
        form = aiohttp.FormData(); form.add_field("image", stream.getvalue(), filename="scene.png", content_type="image/png")
        photo = await (await self.client.post("/api/uploads", data=form)).json()
        url = f"/api/projects/{photo['project_id']}/versions/{photo['id']}/close-up"
        for box in ([0, 0, 20, 20], [500, 300, 200, 200], [0, 0, 400, 50], [0.5, 0, 100, 100]):
            self.assertEqual((await self.client.post(url, json={"box": box})).status, 400, box)
        self.assertEqual((await self.client.post(url, json={"box": [0, 0, 100, 100], "size": "8k"})).status, 400)
        response = await self.client.post(url, json={"box": [200, 100, 180, 200], "size": "standard", "quality": "high"})
        self.assertEqual(response.status, 201)
        source = await response.json()
        self.assertEqual((source["width"] % 16, source["height"] % 16, source["parent_version_id"]), (0, 0, photo["id"]))
        with Image.open(media_file(self.store.directory, "uploads", source["id"])) as saved, Image.open(io.BytesIO(stream.getvalue())) as original:
            expected = original.convert("RGB").crop((200, 100, 380, 300)).resize(saved.size, Image.Resampling.LANCZOS)
            self.assertLess(float(np.abs(np.asarray(saved, dtype=np.float32) - np.asarray(expected, dtype=np.float32)).mean()), 2)
        # A close-up cannot be smuggled in with an ordinary source.
        self.assertEqual((await self.client.post("/api/generate", json={"mode": "edit", "prompt": "x", "source_id": photo["id"], "close_up": {}})).status, 400)
        response = await self.client.post("/api/generate", json={"mode": "edit", "prompt": "Close-up", "quality": "high", "source_id": source["id"],
                                                                  "project_id": photo["project_id"], "snofs": True, "close_up": {"detail": "faithful", "subject": "face"}})
        self.assertEqual(response.status, 202)
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual((recipe["snofs"], recipe["aspect"], recipe["resize_mode"], recipe["output_size"]), (False, "portrait", "native", "original"))
        self.assertEqual((recipe["generation_width"], recipe["generation_height"]), (source["width"], source["height"]))
        self.assertEqual((recipe["close_up"]["from"], recipe["close_up"]["subject"], recipe["parent_image_id"]), ([200, 100, 180, 200], "face", None))
        self.assertIn("The subject is: face.", recipe["prompt"])
        project = await (await self.client.get(f"/api/projects/{photo['project_id']}")).json()
        close_up = project["versions"][-1]
        self.assertEqual((close_up["parent"]["id"], close_up["parent"]["url"]), (photo["id"], f"/media/upload/{source['id']}"))
        # 2K close-ups are generated near one megapixel, then enlarged to their saved size.
        large = await (await self.client.post(url, json={"box": [200, 100, 180, 200], "size": "2k", "quality": "fast"})).json()
        response = await self.client.post("/api/generate", json={"mode": "edit", "prompt": "Close-up", "quality": "fast", "source_id": large["id"], "close_up": {"detail": "detailed"}})
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual((recipe["width"], recipe["height"], recipe["resize_mode"]), (1843, 2048, "auto"))
        self.assertLess(recipe["generation_width"] * recipe["generation_height"], 1_100_000)
        self.assertEqual(json.dumps(recipe["close_up"]["detail"]), '"detailed"')

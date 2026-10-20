import base64
import io
import json
import unittest

import aiohttp
import numpy as np
from PIL import Image, ImageDraw

from studio.identity import DONOR_SIDE, blend_width, grow, body_guide_image, donor_body, donor_crops, engine_donor, identity_hint, swap_areas, validate_parts, request_parts, swap_hint
from studio.metrics import guided
from studio.engine import head_pose_mask
from studio.identity import neutralize_clothing, refinement_recipe
from studio.server import STATE
from studio.tests import test_projects as project_fixtures
from studio.tests import test_studio as engine_fixtures


def rows(*boxes, size):
    result = []
    for box in boxes:
        image = Image.new("L", size, 0)
        ImageDraw.Draw(image).rectangle(box, fill=255)
        stream = io.BytesIO(); image.save(stream, "PNG")
        result.append({"png": base64.b64encode(stream.getvalue()).decode(), "score": .9, "box": list(box)})
    return result


def sam(size=(400, 600), person=(120, 60, 280, 590), face=(170, 90, 230, 170), hair=(160, 60, 240, 110), extra=(), skin=()):
    """A SAM 3 reply on its preview grid: one main person, optionally small background people."""
    return {"width": size[0], "height": size[1], "candidates": [], "skin": rows(*skin, size=size), "seconds": 4, "cached": False,
            "faces": rows(*([face] if face else []), *[e["face"] for e in extra], size=size),
            "concepts": {"hair": rows(*([hair] if hair else []), size=size),
                         "person": rows(*([person] if person else []), *[e["person"] for e in extra], size=size)}}


def boxed(size, box):
    mask = np.zeros(size[::-1], bool); mask[box[1]:box[3] + 1, box[0]:box[2] + 1] = True
    return mask


class IdentityRegionTests(unittest.TestCase):
    def test_public_modes_are_exclusive_and_legacy_parts_still_work(self):
        self.assertEqual(request_parts({"mode": "head"}), ["head"])
        self.assertEqual(request_parts({"mode": "whole"}), ["face", "head", "body", "clothes"])
        self.assertEqual(request_parts({"parts": ["face"]}), ["face"])
        for payload in ({"mode": "face"}, {"mode": []}, {"mode": "head", "parts": ["clothes"]}):
            with self.assertRaises(ValueError):
                request_parts(payload)

    def test_held_glass_and_overlapping_bystander_are_outside_both_passes(self):
        result = sam(extra=[{"person": (285, 40, 320, 500), "face": (288, 45, 310, 75)}])
        result["candidates"] = rows((225, 150, 255, 310), size=(400, 600))
        for parts in (["head"], ["face", "head", "body", "clothes"]):
            areas = swap_areas(result, parts)
            self.assertFalse(areas["area"][150:311, 225:256].any())
            self.assertFalse(areas["core"][150:311, 225:256].any())
            self.assertFalse(areas["area"][40:501, 285:321].any())
            self.assertTrue(areas["summary"]["objects_protected"])

    def test_long_hair_does_not_expand_head_edit_over_the_torso(self):
        areas = swap_areas(sam(hair=(160, 60, 240, 480)), ["head"])
        self.assertFalse(areas["area"][300, 280])
        self.assertTrue(areas["core"][300, 200])

    def test_new_head_reserves_room_for_donor_hair_but_keeps_objects(self):
        target = sam()
        reference = sam(hair=(145, 60, 255, 360))
        target["candidates"] = rows((225, 230, 250, 290), size=(400, 600))
        areas = swap_areas(target, ["head"], reference)
        self.assertTrue(areas["area"][300, 150])
        self.assertFalse(areas["area"][230:291, 225:251].any())
        self.assertFalse(areas["core"][300, 150])  # room for new hair is not original head anatomy

    def test_body_reference_does_not_require_a_face(self):
        self.assertEqual(len(donor_crops(sam(face=None), ["clothes"], 800, 1200)), 1)

    def test_new_prompts_assign_pose_to_original_without_suggesting_a_beard(self):
        for mode in ("head", "whole"):
            hint = swap_hint(mode)
            self.assertIn("head rotation, tilt, gaze direction", hint)
            self.assertIn("grip and contact with existing objects", hint)
            self.assertNotIn("glass", hint)
            self.assertNotIn("phone", hint)
            self.assertNotIn("beard", hint)
            self.assertNotIn("mustache", hint)
        self.assertNotEqual(guided({"identity_swap": {"mode": "whole"}}), guided({"identity_swap": {"mode": "head"}}))
        self.assertNotEqual(guided({"identity_swap": {"mode": "whole"}}), guided({"identity_swap": {}}))

    def test_parts_are_validated_and_kept_in_canonical_order(self):
        self.assertEqual(validate_parts(["clothes", "face"]), ["face", "clothes"])
        for parts in (None, [], ["face", "face"], ["nose"], "face"):
            with self.assertRaises(ValueError):
                validate_parts(parts)

    def test_face_swap_changes_only_the_face_and_protects_hair(self):
        result = sam()
        areas = swap_areas(result, ["face"])
        face, hair = boxed((400, 600), (170, 90, 230, 170)), boxed((400, 600), (160, 60, 240, 110))
        # The hair is never changed; an oval around the features is replaced and the rest of the face fades.
        self.assertFalse((areas["area"] & hair).any())
        self.assertTrue(areas["area"][face & ~hair].all())
        self.assertTrue(areas["core"][140, 200] and areas["core"][120, 200])  # nose and eyes
        self.assertFalse(areas["core"][115, 172] or areas["core"][168, 172])  # temple and jaw corner fade instead
        self.assertFalse((areas["core"] & ~face).any())
        # Only a few pixels of room, so Flux cannot repaint the temples with the reference's hair.
        self.assertLessEqual(areas["area"].sum() - (face & ~hair).sum(), 1200)
        self.assertTrue(areas["protected"][hair].all())

    def test_a_face_alone_fades_gradually(self):
        self.assertEqual(blend_width(["face"], [100, 100, 300, 360], 9), 39)
        self.assertEqual(blend_width(["face"], [0, 0, 2000, 2400], 9), 64)
        self.assertEqual(blend_width(["face", "clothes"], [100, 100, 300, 360], 9), 9)

    def test_clothes_and_body_keep_face_and_hair_exactly(self):
        for parts in (["clothes"], ["body"], ["body", "clothes"]):
            areas = swap_areas(sam(), parts)
            head = boxed((400, 600), (170, 90, 230, 170)) | boxed((400, 600), (160, 60, 240, 110))
            self.assertFalse((areas["area"] & head).any(), parts)
            self.assertTrue(areas["core"][boxed((400, 600), (130, 300, 270, 580))].all())
            # Room beyond the old outline for a different build or garment, but none beside the kept head,
            # where Flux would paint the reference's hair.
            self.assertTrue(areas["area"][300, 113])
            near_head = grow(head, 0.25 * 110) & ~boxed((400, 600), (120, 60, 280, 590))
            self.assertFalse((areas["area"] & near_head).any(), parts)

    def test_head_and_whole_identity_include_hair_and_room_for_a_new_hairstyle(self):
        head = swap_areas(sam(), ["head"])
        self.assertTrue(head["core"][boxed((400, 600), (160, 60, 240, 110))].all())
        self.assertTrue(head["area"][130, 255])  # beside the old hair, for fuller new hair
        self.assertFalse(head["protected"].any())
        whole = swap_areas(sam(), ["face", "head", "body", "clothes"])
        self.assertTrue(whole["core"][boxed((400, 600), (120, 60, 280, 590))].all())
        self.assertTrue(whole["area"][300, 100])

    def test_main_person_wins_over_small_background_people(self):
        bystander = {"person": (10, 20, 40, 120), "face": (15, 22, 35, 45)}
        areas = swap_areas(sam(extra=[bystander]), ["face", "head", "body", "clothes"])
        self.assertFalse(areas["area"][30:120, 10:40].any())
        self.assertEqual(areas["summary"]["person_box"], [120, 60, 281, 591])

    def test_missing_people_and_faces_are_explained(self):
        with self.assertRaisesRegex(ValueError, "No person"):
            swap_areas(sam(person=None, face=None, hair=None), ["clothes"])
        with self.assertRaisesRegex(ValueError, "No face"):
            swap_areas(sam(face=None), ["face"])
        self.assertTrue(swap_areas(sam(face=None), ["clothes"])["area"].any())
        with self.assertRaisesRegex(ValueError, "reference photo"):
            donor_crops(sam(face=None), ["head"], 800, 1200)

    def test_reference_crop_is_tight_for_a_face_and_the_whole_person_for_outfits(self):
        (x, y, w, h), = donor_crops(sam(), ["face"], 800, 1200)
        # The face box (170..230, 90..170 on the 400x600 preview) doubled, with a small margin.
        self.assertTrue(x <= 340 and y <= 180 and x + w >= 462 and y + h >= 342)
        self.assertLessEqual(w * h, 1.45 * 122 * 162)
        person, = donor_crops(sam(), ["clothes"], 800, 1200)
        self.assertLessEqual(person[0], 240); self.assertGreaterEqual(person[0] + person[2], 562)
        self.assertLessEqual(person[1], 120); self.assertEqual(person[1] + person[3], 1200)
        # One reference for an outfit with a face; an added close-up made Flux keep the old outfit.
        self.assertEqual(donor_crops(sam(), ["face", "clothes"], 800, 1200), [person])
        self.assertEqual(donor_crops(sam(), ["face", "head", "body", "clothes"], 800, 1200), [person])
        # A body guide has no face, so a face swap with it adds the reference's face as image 3.
        self.assertEqual(donor_crops(sam(), ["face", "body"], 800, 1200), [person, [x, y, w, h]])
        head = donor_crops(sam(), ["head", "body"], 800, 1200)[1]
        self.assertGreater(head[2] * head[3], w * h)  # the head crop includes hair

    def test_prompts_name_what_to_take_and_what_to_keep(self):
        face = identity_hint(["face"])
        self.assertIn("Do not copy any hair from image 2", face)
        self.assertIn("Keep from image 1: the hairstyle and hair color; the body shape and proportions; the outfit.", face)
        clothes = identity_hint(["clothes"])
        self.assertIn("the outfit (the same garments", clothes)
        self.assertIn("Keep from image 1: the face and hairstyle; the body shape and proportions.", clothes)
        head = identity_hint(["face", "head"])
        self.assertEqual(head.count("the whole head"), 1)
        self.assertIn("Remove the original hairstyle", head)
        whole = identity_hint(["face", "head", "body", "clothes"])
        self.assertIn("Replace the person in image 1 with the person from image 2", whole)
        self.assertNotIn("Image 3", whole)
        self.assertNotIn("Keep from image 1", whole)
        self.assertNotIn("Image 3", identity_hint(["face", "clothes"]))
        self.assertNotIn("Image 3", face)
        # A body swap that keeps the outfit uses a silhouette guide, and the face then comes from image 3.
        body = identity_hint(["body"])
        self.assertIn("Image 2 is a body guide", body)
        self.assertIn("keep wearing exactly the clothes from image 1", body)
        self.assertNotIn("Image 3", body)
        face_body = identity_hint(["face", "body"])
        self.assertIn("Image 3 shows the same person's face", face_body)
        self.assertIn("of the person in image 3", face_body)
        self.assertIn("Do not copy any hair from image 3", face_body)
        self.assertIn("beard or mustache only if image 3 has one", face_body)
        self.assertIn("beard or mustache only if image 2 has one", face)
        self.assertNotIn("body guide", identity_hint(["body", "clothes"]))

    def test_body_guide_shows_build_and_skin_tone_but_no_clothing(self):
        photo = Image.new("RGB", (400, 600), (30, 60, 200))  # blue clothing everywhere
        ImageDraw.Draw(photo).rectangle((170, 90, 230, 170), fill=(190, 140, 110))  # face skin
        ImageDraw.Draw(photo).rectangle((125, 300, 140, 420), fill=(185, 135, 105))  # bare arm
        stream = io.BytesIO(); photo.save(stream, "PNG"); stream.seek(0)
        silhouette, skin = donor_body(sam(skin=[(125, 300, 140, 420)]), stream)
        self.assertTrue(silhouette[300, 200])
        self.assertTrue(all(abs(a - b) <= 6 for a, b in zip(skin, (188, 138, 108))), skin)
        with self.assertRaisesRegex(ValueError, "No visible skin"):
            donor_body(sam(face=None), io.BytesIO(stream.getvalue()))


class IdentityAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = project_fixtures.ProjectTests.asyncSetUp
    asyncTearDown = project_fixtures.ProjectTests.asyncTearDown

    async def photo(self, size, color):
        stream = io.BytesIO(); Image.new("RGB", size, color).save(stream, "PNG")
        form = aiohttp.FormData(); form.add_field("image", stream.getvalue(), filename="person.png", content_type="image/png")
        return await (await self.client.post("/api/uploads", data=form)).json()

    async def test_areas_are_saved_before_generating_and_identity_recipe_is_validated(self):
        target, donor = await self.photo((800, 1200), "navy"), await self.photo((600, 900), "coral")
        calls = []
        async def select(path, query, protect, skin, concepts=()):
            self.assertTrue(self.app[STATE]["engine"].resource_lock.locked())
            calls.append((path.stem, query, protect, skin, tuple(concepts)))
            return sam()
        self.app[STATE]["automask"].select = select
        path = f"/api/uploads/{target['id']}/identity-areas"
        response = await self.client.post(path, json={"donor_id": donor["id"], "parts": ["clothes", "face"]})
        self.assertEqual(response.status, 200)
        areas = await response.json()
        self.assertEqual(calls, [(target["id"], "", True, False, ("hair", "person")), (donor["id"], "", True, False, ("hair", "person"))])
        self.assertEqual(areas["parts"], ["face", "clothes"])
        self.assertEqual({row["id"] for row in self.store.rows("SELECT id FROM masks WHERE source_id=?", (target["id"],))}, {areas["mask"]["id"], areas["core"]["id"]})
        self.assertEqual((await self.client.get(areas["mask"]["url"])).status, 200)
        self.assertEqual(self.store.rows("SELECT * FROM jobs"), [])
        self.assertEqual(len(areas["donor_crops"]), 1)
        identity = {"parts": ["face", "clothes"], "donor_id": donor["id"], "donor_crops": areas["donor_crops"], "notes": "Keep the smile"}
        base = {"mode": "edit", "prompt": "Identity swap · face + clothes", "source_id": target["id"], "quality": "balanced",
                "aspect": "portrait", "mask_id": areas["mask"]["id"], "mask_core_id": areas["core"]["id"], "identity_swap": identity}
        for change in ({"identity_swap": dict(identity, donor_id=target["id"])}, {"identity_swap": dict(identity, donor_crops=[[0, 0, 601, 900]])},
                       {"identity_swap": dict(identity, donor_crops=areas["donor_crops"] * 2)},
                       {"identity_swap": dict(identity, parts=[])}, {"mask_core_id": None}, {"mask_preserve_shape": True},
                       {"identity_swap": dict(identity, notes=7)}):
            self.assertEqual((await self.client.post("/api/generate", json=dict(base, **change))).status, 400, change)
        response = await self.client.post("/api/generate", json=base)
        self.assertEqual(response.status, 202)
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        swap = recipe["identity_swap"]
        self.assertEqual((swap["parts"], swap["notes"], swap["donor_id"], swap["donor"]["width"]), (["face", "clothes"], "Keep the smile", donor["id"], 600))
        self.assertEqual(recipe["masked_edit"]["core_mask_id"], areas["core"]["id"])
        # Balanced works at 768 pixels: large areas are reduced, as in other selected-area edits.
        self.assertEqual(max(recipe["generation_width"], recipe["generation_height"]), 768)
        self.assertGreater(max(recipe["masked_edit"]["crop"][2:]), 768)
        # A small face is enlarged to the working side so Flux can draw real detail.
        face = await (await self.client.post(path, json={"donor_id": donor["id"], "parts": ["face"]})).json()
        response = await self.client.post("/api/generate", json=dict(base, mask_id=face["mask"]["id"], mask_core_id=face["core"]["id"],
                                                                     identity_swap=dict(identity, parts=["face"], donor_crops=face["donor_crops"])))
        small = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual(max(small["generation_width"], small["generation_height"]), 768)
        self.assertLess(max(small["masked_edit"]["crop"][2:]), 768)
        estimate = await (await self.client.post("/api/estimate", json={"mode": "edit", "source_id": target["id"], "quality": "balanced", "aspect": "portrait",
                                                                        "mask_bounds": areas["mask"]["bounds"], "identity_swap": True})).json()
        self.assertEqual(estimate["samples"], 0)

    async def test_area_errors_are_clear_and_release_the_engine(self):
        target, donor = await self.photo((400, 600), "navy"), await self.photo((400, 600), "coral")
        path = f"/api/uploads/{target['id']}/identity-areas"
        async def select(*args, **kwargs):
            return sam(person=None, face=None, hair=None)
        self.app[STATE]["automask"].select = select
        for payload in ({"donor_id": donor["id"], "parts": ["nose"]}, {"donor_id": "missing", "parts": ["face"]}, {"donor_id": target["id"], "parts": ["face"]}):
            self.assertEqual((await self.client.post(path, json=payload)).status, 400, payload)
        response = await self.client.post(path, json={"donor_id": donor["id"], "parts": ["face"]})
        self.assertEqual(response.status, 400)
        self.assertIn("No person was found", (await response.json())["error"])
        engine = self.app[STATE]["engine"]
        self.assertFalse(engine.resource_lock.locked())
        async with engine.resource_lock:
            self.assertEqual((await self.client.post(path, json={"donor_id": donor["id"], "parts": ["face"]})).status, 409)
        self.assertEqual((await self.client.post("/api/uploads/missing/identity-areas", json={"donor_id": donor["id"], "parts": ["face"]})).status, 404)

    async def test_whole_mode_requires_head_detail_masks_from_the_same_photo(self):
        target, donor = await self.photo((400, 600), "navy"), await self.photo((400, 600), "coral")
        calls = []
        async def select(path, query, *args):
            calls.append(query)
            result = sam()
            result["candidates"] = rows((225, 200, 250, 290), size=(400, 600)) if query else []
            return result
        self.app[STATE]["automask"].select = select
        areas = await (await self.client.post(f"/api/uploads/{target['id']}/identity-areas", json={"mode": "whole", "donor_id": donor["id"], "keep_object": "glass"})).json()
        self.assertEqual(calls, ["glass", ""])
        self.assertEqual(len(areas["donor_crops"]), 2)
        swap = {"mode": "whole", "donor_id": donor["id"], "donor_crops": areas["donor_crops"], "keep_object": "glass"}
        base = {"mode": "edit", "prompt": "Whole identity", "source_id": target["id"], "quality": "balanced",
                "mask_id": areas["mask"]["id"], "mask_core_id": areas["core"]["id"], "identity_swap": swap}
        self.assertEqual((await self.client.post("/api/generate", json=base)).status, 400)
        swap["head_pass"] = {"mask_id": areas["head_pass"]["mask"]["id"], "core_mask_id": areas["head_pass"]["core"]["id"]}
        response = await self.client.post("/api/generate", json=base)
        self.assertEqual(response.status, 202, await response.text())
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        detail = recipe["identity_swap"]["head_pass"]
        self.assertFalse(recipe["snofs"])
        self.assertIsNone(recipe["adapter"])
        self.assertLess(detail["masked_edit"]["crop"][3], recipe["masked_edit"]["crop"][3])
        self.assertEqual(recipe["identity_swap"]["keep_object"], "glass")
        swap["head_pass"]["mask_id"] = "missing"
        self.assertEqual((await self.client.post("/api/generate", json=base)).status, 400)


class IdentityEngineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = engine_fixtures.EngineTests.asyncSetUp
    asyncTearDown = engine_fixtures.EngineTests.asyncTearDown
    wait_images = engine_fixtures.EngineTests.wait_images
    photo = IdentityAPITests.photo

    async def test_whole_worker_composes_head_body_and_protected_glass(self):
        target, donor = await self.photo((400, 600), "coral"), await self.photo((400, 600), "seagreen")
        async def select(path, query, *args):
            result = sam()
            result["candidates"] = rows((225, 150, 250, 280), size=(400, 600)) if query else []
            return result
        self.app[STATE]["automask"].select = select
        areas = await (await self.client.post(f"/api/uploads/{target['id']}/identity-areas", json={"mode": "whole", "donor_id": donor["id"]})).json()
        response = await self.client.post("/api/generate", json={"mode": "edit", "prompt": "Whole identity", "source_id": target["id"], "quality": "fast",
            "mask_id": areas["mask"]["id"], "mask_core_id": areas["core"]["id"], "mask_blend_mode": "manual", "mask_feather": 0,
            "mask_color_match": False, "mask_align": False,
            "identity_swap": {"mode": "whole", "donor_id": donor["id"], "donor_crops": areas["donor_crops"],
                              "appearance_mask": {"mask_id": areas["appearance_mask"]["id"]},
                              "head_pass": {"mask_id": areas["head_pass"]["mask"]["id"], "core_mask_id": areas["head_pass"]["core"]["id"]}}})
        self.assertEqual(response.status, 202, await response.text())
        image, = await self.wait_images(1)
        graph = self.submitted[0]["prompt"]
        self.assertEqual(graph["102"]["inputs"]["image"], ["124", 0])
        self.assertEqual(graph["121"]["inputs"]["destination"], ["13", 0])  # new outfit is the context
        self.assertEqual(graph["120"]["inputs"]["image"], ["5", 0])  # original head determines pose
        self.assertEqual(graph["121"]["inputs"]["mask"], ["125", 1])
        recipe = image["recipe"]
        original = Image.new("RGB", (400, 600), "coral")
        neutral = neutralize_clothing(original, self.app[STATE]["store"].directory, recipe)
        self.assertEqual(neutral.getpixel((200, 130)), original.getpixel((200, 130)))  # head pose
        self.assertEqual(neutral.getpixel((240, 230)), original.getpixel((240, 230)))  # glass
        self.assertNotEqual(neutral.getpixel((200, 400)), original.getpixel((200, 400)))  # old clothes removed
        self.assertIs(neutralize_clothing(original, self.app[STATE]["store"].directory, refinement_recipe(recipe)), original)
        selection = recipe["identity_swap"]["head_pass"]["masked_edit"]
        x, y, w, h = selection["crop"]
        with Image.open(io.BytesIO(head_pose_mask(self.app[STATE]["store"].directory, recipe))) as pose, \
             Image.open(self.app[STATE]["store"].directory / "masks" / (selection["core_mask_id"] + ".png")) as core:
            self.assertTrue(np.array_equal(255 - np.asarray(pose.getchannel("A")), np.asarray(core.crop((x, y, x+w, y+h)))))
        self.assertIn("-head-reference.png", graph["114"]["inputs"]["image"])
        self.assertIn("-identity-2.png", graph["102"]["inputs"]["image_paths"])
        self.assertNotIn("-identity-2.png", graph["2"]["inputs"]["image_paths"])
        content = await (await self.client.get(f"/media/image/{image['id']}")).read()
        with Image.open(io.BytesIO(content)) as output:
            self.assertEqual(output.getpixel((200, 130)), (255, 215, 0))  # detailed head
            self.assertEqual(output.getpixel((200, 400)), (0, 0, 128))  # replaced body
            self.assertEqual(output.getpixel((240, 230)), (255, 127, 80))  # original glass
            pixels = np.asarray(output)
            workflow = json.loads(output.info["workflow"])
        with Image.open(self.app[STATE]["store"].directory / "masks" / (areas["mask"]["id"] + ".png")) as mask:
            self.assertTrue((pixels[np.asarray(mask) == 0] == (255, 127, 80)).all())
        nodes = {node["id"]: node for node in workflow["nodes"]}
        self.assertEqual(len(nodes), len(workflow["nodes"]))
        for link_id, origin, slot, target, target_slot, _ in workflow["links"]:
            self.assertIn(link_id, nodes[origin]["outputs"][slot]["links"])
            self.assertEqual(nodes[target]["inputs"][target_slot]["link"], link_id)

    async def test_worker_adds_the_reference_crop_and_keeps_every_unchosen_pixel(self):
        uploads = []
        for color in ("coral", "seagreen"):
            stream = io.BytesIO(); Image.new("RGB", (400, 600), color).save(stream, "PNG")
            form = aiohttp.FormData(); form.add_field("image", stream.getvalue(), filename="person.png", content_type="image/png")
            uploads.append(await (await self.client.post("/api/uploads", data=form)).json())
        target, donor = uploads
        async def select(*args, **kwargs):
            return sam()
        self.app[STATE]["automask"].select = select
        areas = await (await self.client.post(f"/api/uploads/{target['id']}/identity-areas", json={"donor_id": donor["id"], "parts": ["clothes"]})).json()
        response = await self.client.post("/api/generate", json={"mode": "edit", "prompt": "Identity swap · clothes", "source_id": target["id"], "quality": "fast",
            "aspect": "portrait", "mask_id": areas["mask"]["id"], "mask_core_id": areas["core"]["id"], "mask_blend_mode": "manual", "mask_feather": 0,
            "mask_color_match": False, "mask_align": False,
            "identity_swap": {"parts": ["clothes"], "donor_id": donor["id"], "donor_crops": areas["donor_crops"], "notes": "Add a belt"}})
        self.assertEqual(response.status, 202)
        image, = await self.wait_images(1)
        inputs = self.submitted[0]["prompt"]["2"]["inputs"]
        reference, = [name for name in self.upload_names if "-identity-" in name]
        self.assertEqual(json.loads(inputs["image_paths"]), ["/test/project/runtime/input/web-studio/" + reference])
        self.assertTrue(inputs["prompt"].startswith("Image 1 is the photo to edit."))
        self.assertTrue(inputs["prompt"].endswith("Additional notes: Add a belt"))
        content = await (await self.client.get(f"/media/image/{image['id']}")).read()
        with Image.open(io.BytesIO(content)) as result, Image.open(self.app[STATE]["store"].directory / "masks" / (areas["mask"]["id"] + ".png")) as mask:
            changed = np.asarray(mask.convert("L")) > 0
            pixels = np.asarray(result.convert("RGB"))
            self.assertTrue((pixels[~changed] == (255, 127, 80)).all())
            self.assertTrue((pixels[changed] == (0, 0, 128)).all())

    async def test_reference_crop_is_limited_per_quality(self):
        store = self.app[STATE]["store"]
        Image.new("RGB", (3000, 2000), "coral").save(store.directory / "uploads" / "donor.png")
        for quality, side in DONOR_SIDE.items():
            crops = engine_donor(store.directory, {"quality": quality, "identity_swap": {"donor_id": "donor", "donor_crops": [[100, 50, 2400, 1800], [10, 10, 200, 300]]}})
            with Image.open(io.BytesIO(crops[0])) as image, Image.open(io.BytesIO(crops[1])) as close:
                self.assertEqual(max(image.size), side)
                self.assertEqual((image.width % 16, image.height % 16), (0, 0))
                # A small crop (a distant face) is enlarged to the working side, on the 16px grid.
                self.assertEqual(max(close.size), side)
                self.assertEqual((close.width % 16, close.height % 16), (0, 0))


class BodyGuideTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = project_fixtures.ProjectTests.asyncSetUp
    asyncTearDown = project_fixtures.ProjectTests.asyncTearDown
    photo = IdentityAPITests.photo

    async def test_body_only_swap_sends_a_silhouette_in_the_reference_skin_tone(self):
        target = await self.photo((400, 600), "navy")
        stream = io.BytesIO(); donor_image = Image.new("RGB", (400, 600), (200, 30, 40))  # red jacket
        ImageDraw.Draw(donor_image).rectangle((170, 90, 230, 170), fill=(150, 100, 70))
        donor_image.save(stream, "PNG")
        form = aiohttp.FormData(); form.add_field("image", stream.getvalue(), filename="donor.png", content_type="image/png")
        donor = await (await self.client.post("/api/uploads", data=form)).json()
        skin_requests = []
        async def select(path, query, protect, skin, concepts=()):
            skin_requests.append((path.stem == donor["id"], skin))
            return sam()
        self.app[STATE]["automask"].select = select
        areas = await (await self.client.post(f"/api/uploads/{target['id']}/identity-areas", json={"donor_id": donor["id"], "parts": ["body"]})).json()
        self.assertEqual(skin_requests, [(False, False), (True, True)])
        guide = areas["body_guide"]
        self.assertEqual(self.store.one("SELECT source_id FROM masks WHERE id=?", (guide["mask_id"],))["source_id"], donor["id"])
        self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(guide["skin"], (150, 100, 70))), guide["skin"])
        identity = {"parts": ["body"], "donor_id": donor["id"], "donor_crops": areas["donor_crops"]}
        base = {"mode": "edit", "prompt": "Identity swap · body", "source_id": target["id"], "quality": "fast", "aspect": "portrait",
                "mask_id": areas["mask"]["id"], "mask_core_id": areas["core"]["id"]}
        for broken in (None, {"mask_id": areas["mask"]["id"], "skin": guide["skin"]}, {"mask_id": guide["mask_id"], "skin": [1, 2]}):
            self.assertEqual((await self.client.post("/api/generate", json=dict(base, identity_swap=dict(identity, body_guide=broken)))).status, 400)
        response = await self.client.post("/api/generate", json=dict(base, identity_swap=dict(identity, body_guide=guide)))
        recipe = self.store.job((await response.json())["jobs"][0])["recipe"]
        self.assertEqual(recipe["identity_swap"]["body_guide"]["skin"], guide["skin"])
        reference, = engine_donor(self.store.directory, recipe)
        with Image.open(io.BytesIO(reference)) as image:
            colors = {color for _, color in image.convert("RGB").getcolors(1 << 20)}
        self.assertIn((232, 232, 232), colors)
        self.assertIn(tuple(guide["skin"]), colors)
        self.assertFalse(any(red > 180 and green < 60 for red, green, _ in colors))  # no jacket
        (self.store.directory / "masks" / (guide["mask_id"] + ".png")).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "silhouette has changed"):
            engine_donor(self.store.directory, recipe)

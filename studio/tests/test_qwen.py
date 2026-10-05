import json
import unittest
from pathlib import Path

from studio.engine import QUALITIES, build_graph
from studio.presets import PRESETS, QWEN, recipe_model, reference_resolution
from studio.engine import Engine
from studio.sizing import plan_size

ROOT = Path(__file__).resolve().parents[2]


class QwenGraphTests(unittest.TestCase):
    def recipe(self, preset, output="preset"):
        sizing = plan_size("fast", "square", "edit", {"width": 643, "height": 427}, output,
                           QUALITIES, json.loads((ROOT / "config/upscaler.json").read_text()))
        return {"image_id": "qwen-test", "mode": "edit", "prompt": "Make the cafe awning blue",
                "seed": 42, **recipe_model(preset, QUALITIES["fast"], True, {}, None),
                **sizing, "reference_resolution": reference_resolution(sizing)}

    def test_four_presets_select_only_their_adapter_and_correct_step_count(self):
        for preset in PRESETS:
            if preset == "flux":
                continue
            with self.subTest(preset=preset):
                r = self.recipe(preset)
                graph, workflow = build_graph(r, ROOT, {}, "photo.png")
                loras = [n["inputs"]["lora_name"] for n in graph.values() if n["class_type"] == "LoraLoaderModelOnly"]
                fast = "fast" in preset
                self.assertEqual(graph["20"]["inputs"]["steps"], 8 if fast else 40)
                self.assertEqual(graph["20"]["inputs"]["cfg"], 1 if fast else 4)
                self.assertEqual(len(loras), 2 if fast else 1)
                self.assertEqual(any("Lightning-8steps" in name for name in loras), fast)
                self.assertEqual(any("Snofs" in name for name in loras), "snofs" in preset)
                self.assertEqual(any("nsfw-lora" in name for name in loras), "mcnl" in preset)
                self.assertTrue(graph["1"]["inputs"]["unet_name"].endswith("Q4_0.gguf" if fast else "Q4_K_M.gguf"))
                self.assertEqual(graph["13"]["inputs"]["image1"], ["5", 0])
                self.assertEqual(graph["13"]["inputs"]["reference_resolution"], 512)
                self.assertEqual(graph["14"]["inputs"]["reference_resolution"], 512)
                self.assertTrue(graph["1"]["inputs"]["patch_on_device"])
                self.assertEqual(graph["15"]["inputs"]["reference_latents_method"], "index_timestep_zero")
                self.assertEqual(graph["2"]["inputs"]["images"], ["24", 0])
                self.check_links(workflow)

    def test_reference_budget_follows_native_canvas_and_caps_at_one_megapixel(self):
        self.assertEqual(reference_resolution({"generation_width": 512, "generation_height": 512}), 512)
        self.assertEqual(reference_resolution({"generation_width": 768, "generation_height": 768}), 768)
        self.assertEqual(reference_resolution({"generation_width": 4096, "generation_height": 4096}), 1024)
        legacy = self.recipe("qwen-fast-mcnl")
        del legacy["reference_resolution"]
        del legacy["gguf_settings"]
        graph, _ = build_graph(legacy, ROOT, {}, "photo.png")
        self.assertEqual(graph["13"]["inputs"]["reference_resolution"], 1024)
        self.assertFalse(graph["1"]["inputs"]["patch_on_device"])

    def check_links(self, workflow):
        nodes = {n["id"]: n for n in workflow["nodes"]}
        self.assertEqual(len(nodes), len(workflow["nodes"]))
        self.assertEqual(workflow["last_node_id"], max(nodes))
        for link, origin, output, target, slot, kind in workflow["links"]:
            self.assertIn(link, nodes[origin]["outputs"][output]["links"])
            self.assertEqual(nodes[target]["inputs"][slot]["link"], link)
            self.assertEqual(nodes[origin]["outputs"][output]["type"], kind)

    def test_qwen_preserves_exact_original_and_upscales_to_4k(self):
        for output in ("original", "4k"):
            with self.subTest(output=output):
                r = self.recipe("qwen-fast-mcnl", output)
                graph, workflow = build_graph(r, ROOT, {}, "photo.png")
                self.assertEqual(graph["7"]["inputs"]["image"], ["24", 0])
                self.assertEqual(graph["9"]["inputs"]["images"], ["8", 0])
                self.assertEqual(graph["8"]["inputs"]["width"], 643 if output == "original" else 4096)
                self.assertEqual(graph["8"]["inputs"]["height"], 427 if output == "original" else round(4096*427/643))
                self.check_links(workflow)


class QwenReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_lightning_disables_only_fast_presets_and_missing_patch_disables_qwen(self):
        engine = Engine(None, "http://test", ROOT, {})
        names = {folder: [a["comfy_filename"] for a in QWEN["assets"].values() if a["folder"] == folder]
                 for folder in ("diffusion_models", "text_encoders", "vae", "loras")}
        kinds = {"UnetLoaderGGUFAdvanced": ("unet_name", "diffusion_models"), "CLIPLoaderGGUF": ("clip_name", "text_encoders"),
                 "VAELoader": ("vae_name", "vae"), "LoraLoaderModelOnly": ("lora_name", "loras")}
        patched = True
        async def request(method, path, **kwargs):
            kind = path.rsplit("/", 1)[1]
            if kind == "TextEncodeQwenImageEditPlus":
                return {kind: {"input": {"optional": {"reference_resolution": ["INT"]} if patched else {}}}}
            name, folder = kinds[kind]
            return {kind: {"input": {"required": {name: [names[folder]]}}}}
        engine.request = request
        self.assertTrue(all((await engine.preset_availability()).values()))
        names["loras"].remove(QWEN["assets"]["lightning"]["comfy_filename"])
        engine._preset_cache = None
        ready = await engine.preset_availability()
        self.assertFalse(ready["qwen-fast-mcnl"])
        self.assertFalse(ready["qwen-fast-snofs"])
        self.assertTrue(ready["qwen-quality-mcnl"])
        self.assertTrue(ready["qwen-quality-snofs"])
        patched = False
        engine._preset_cache = None
        self.assertEqual(await engine.preset_availability(), {key: key == "flux" for key in PRESETS})


if __name__ == "__main__":
    unittest.main()

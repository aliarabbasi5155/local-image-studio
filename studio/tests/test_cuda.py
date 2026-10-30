"""Platform boundaries: CUDA recipes must identify their actual weights."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from studio.server import ROOT, STATE, create_app
from scripts.video_native import NativeVideo


class CUDAConfigurationTests(unittest.TestCase):
    def test_container_backend_records_cuda_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STUDIO_BACKEND": "cuda"}):
            app = create_app(data_dir=directory, pin="123456", start_worker=False)
            state = app[STATE]
            try:
                for bits in ("4", "8"):
                    source = state["models"][f"flux2-klein-9b-{bits}bit"]
                    self.assertEqual(source["backend"], "cuda-gguf")
                    self.assertEqual(source["repo_id"], "unsloth/FLUX.2-klein-9B-GGUF")
                    self.assertEqual(source["download_bytes"], source["size"])
                    self.assertIn("sha256", source["text_encoder"])
                self.assertEqual(state["runtime"]["backend"], "cuda")
                self.assertNotIn("flux2klein_mlx", state["runtime"])
            finally:
                state["store"].db.close()

    def test_video_requires_every_verified_component_and_preserves_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/backend.txt").write_text("cuda\n")
            (root / "config").mkdir()
            shutil.copy2(ROOT / "config/video.cuda.json", root / "config/video.cuda.json")
            config = json.loads((root / "config/video.cuda.json").read_text())
            native = NativeVideo(root, None, None, None)
            self.assertFalse(native.ready())
            (root / "runtime/video-cuda-ready.json").write_text(json.dumps(config))
            for key in ("model", "text_encoder", "projection", "video_vae", "audio_vae", "upscaler"):
                source = config[key]
                path = root / "runtime/ComfyUI/models" / source["category"] / Path(source["filename"]).name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").touch()
            with patch("scripts.video_native.shutil.which", return_value="/usr/bin/ffmpeg"):
                self.assertTrue(native.ready())
                path.unlink()
                self.assertFalse(native.ready())
            recipe = {"prompt": "Slow camera movement", "frames": 145, "seed": 123, "width": 512, "height": 512}
            command = native.command(recipe, Path("input.png"), Path("output.mp4"))
            self.assertEqual(json.loads(command[command.index("--recipe") + 1]), recipe)
            self.assertTrue(command[1].endswith("scripts/video_cuda.py"))

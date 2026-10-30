#!/usr/bin/env python3
"""Isolated, staged CUDA LTX 2.3 inference; exits to release all model memory."""
import argparse
import asyncio
import gc
import json
import logging
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/video.cuda.json").read_text())
STAGE_ONE = CONFIG["sampling"]["stage_one_sigmas"]
STAGE_TWO = CONFIG["sampling"]["stage_two_sigmas"]


def filename(key):
    return Path(CONFIG[key]["filename"]).name


def release():
    import comfy.model_management as memory
    memory.unload_all_models()
    gc.collect()
    memory.soft_empty_cache()


def text_conditioning(prompt, fps):
    import nodes
    from comfy_extras.nodes_lt import LTXVConditioning
    clip = nodes.NODE_CLASS_MAPPINGS["DualCLIPLoaderGGUF"]().load_clip(
        filename("text_encoder"), filename("projection"), type="ltxv")[0]
    positive = nodes.CLIPTextEncode().encode(clip, prompt)[0]
    return LTXVConditioning.execute(positive, positive, fps)[0]


def initial_latents(image, width, height, frames, fps):
    import nodes
    from comfy_extras.nodes_lt import EmptyLTXVLatentVideo, LTXVImgToVideoInplace
    from comfy_extras.nodes_lt_audio import LTXVEmptyLatentAudio
    vae = nodes.VAELoader().load_vae(filename("video_vae"))[0]
    latent = EmptyLTXVLatentVideo.execute(width, height, frames)[0]
    video = LTXVImgToVideoInplace.execute(vae, image, latent, strength=1.0)[0]
    del vae
    release()
    audio_vae = nodes.VAELoader().load_vae(filename("audio_vae"))[0]
    audio = LTXVEmptyLatentAudio.execute(frames, fps, 1, audio_vae)[0]
    return video, audio


def denoise(video, audio, positive, sigmas, seed):
    import nodes
    import torch
    from comfy_extras.nodes_custom_sampler import BasicGuider, KSamplerSelect, RandomNoise, SamplerCustomAdvanced
    from comfy_extras.nodes_lt import LTXVConcatAVLatent, LTXVSeparateAVLatent
    model = nodes.NODE_CLASS_MAPPINGS["UnetLoaderGGUF"]().load_unet(filename("model"), patch_on_device=True)[0]
    guider = BasicGuider.execute(model, positive)[0]
    noise = RandomNoise.execute(seed)[0]
    sampler = KSamplerSelect.execute("euler")[0]
    av = LTXVConcatAVLatent.execute(video, audio)[0]
    sampled = SamplerCustomAdvanced.execute(noise, guider, sampler, torch.tensor(sigmas), av)[0]
    separated = LTXVSeparateAVLatent.execute(sampled)
    return separated[0], separated[1]


def upscale(video, image):
    import nodes
    from comfy_extras.nodes_lt import LTXVImgToVideoInplace
    from comfy_extras.nodes_lt_upsampler import LTXVLatentUpsampler
    vae = nodes.VAELoader().load_vae(filename("video_vae"))[0]
    loader = nodes.NODE_CLASS_MAPPINGS["LatentUpscaleModelLoader"]
    model = loader.execute(filename("upscaler"))[0]
    latent = LTXVLatentUpsampler.execute(video, model, vae)[0]
    return LTXVImgToVideoInplace.execute(vae, image, latent, strength=1.0)[0]


def save_audio(latent, output):
    import nodes
    import numpy as np
    from comfy_extras.nodes_lt_audio import LTXVAudioVAEDecode
    vae = nodes.VAELoader().load_vae(filename("audio_vae"))[0]
    audio = LTXVAudioVAEDecode.execute(latent, vae)[0]
    waveform = audio["waveform"][0].clamp(-1, 1).cpu().numpy()
    pcm = (waveform.T * 32767).astype(np.dtype("<i2"))
    with wave.open(str(output), "wb") as target:
        target.setnchannels(waveform.shape[0])
        target.setsampwidth(2)
        target.setframerate(audio["sample_rate"])
        target.writeframes(pcm.tobytes())


def save_video(latent, audio, output, fps):
    import nodes
    import numpy as np
    vae = nodes.VAELoader().load_vae(filename("video_vae"))[0]
    frames = nodes.VAEDecodeTiled().decode(vae, latent, tile_size=256, overlap=64,
                                          temporal_size=16, temporal_overlap=8)[0]
    del vae
    release()
    audio_file = output.with_suffix(".wav")
    save_audio(audio, audio_file)
    release()
    height, width = frames.shape[1:3]
    print(f"Encoding {len(frames)} video frames at {fps} fps", flush=True)
    command = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-i", str(audio_file),
               "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-af", "apad", "-frames:v", str(len(frames)),
               "-shortest", "-movflags", "+faststart", str(output)]
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
            try:
                for frame in frames:
                    process.stdin.write((frame.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8).tobytes())
                process.stdin.close()
                if process.wait():
                    raise RuntimeError("FFmpeg could not encode the generated video.")
            except BaseException:
                process.kill()
                raise
    finally:
        audio_file.unlink(missing_ok=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    comfy = ROOT / "runtime/ComfyUI"
    sys.path.insert(0, str(comfy))
    sys.argv = [str(comfy / "main.py"), "--lowvram", "--reserve-vram", "1",
                "--disable-pinned-memory", "--preview-method", "none", "--offline"]
    import comfy.options
    comfy.options.enable_args_parsing()
    import nodes
    import torch
    import numpy as np
    from PIL import Image
    asyncio.run(nodes.init_extra_nodes(init_api_nodes=False))
    if args.probe:
        required = ["UnetLoaderGGUF", "DualCLIPLoaderGGUF", "LatentUpscaleModelLoader"]
        assert all(name in nodes.NODE_CLASS_MAPPINGS for name in required), required
        print("CUDA video runtime ready:", torch.cuda.get_device_name(), flush=True)
        return
    if not args.recipe or not args.image or not args.output:
        parser.error("--recipe, --image and --output are required")
    recipe = json.loads(args.recipe)
    width = recipe.get("generation_width", recipe["width"])
    height = recipe.get("generation_height", recipe["height"])
    with Image.open(args.image) as photo:
        image = torch.from_numpy(np.asarray(photo.convert("RGB"), dtype=np.float32) / 255).unsqueeze(0)
    with torch.inference_mode():
        print("Encoding video prompt", flush=True)
        positive = text_conditioning(recipe["prompt"], recipe["fps"])
        release()
        video, audio = initial_latents(image, width // 2, height // 2, recipe["frames"], recipe["fps"])
        release()
        print("Video stage 1: eight steps at half resolution", flush=True)
        video, audio = denoise(video, audio, positive, STAGE_ONE, recipe["seed"])
        release()
        video = upscale(video, image)
        release()
        print("Video stage 2: three steps at full resolution", flush=True)
        video, audio = denoise(video, audio, positive, STAGE_TWO, recipe["seed"])
        release()
        save_video(video, audio, args.output, recipe["fps"])
        print("Video saved:", args.output, flush=True)


if __name__ == "__main__":
    main()

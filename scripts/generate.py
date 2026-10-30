#!/usr/bin/env python3
"""Generate through ComfyUI and record end-to-end latency and PNG validation."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.backends import is_cuda, model_registry, runtime_registry  # noqa: E402
DEFAULT_PROMPT = (
    "Editorial architectural photograph of a secluded Italian villa beside Lake Como, "
    "warm terracotta walls, weathered stone steps, delicate climbing ivy and purple "
    "wisteria, a small wooden boat moored at the shore, distant mountains reflected "
    "in still turquoise water. Soft golden sunrise with thin morning mist, natural "
    "color, realistic fine textures, balanced composition, photographed on a medium "
    "format camera, 50mm lens, crisp details."
)


def request(base, route, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(base + route, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def resolve_lora(model_key, lora_key, strength=None):
    if lora_key is None:
        return None
    adapters = json.loads((ROOT / "config/loras.json").read_text())
    models = model_registry(ROOT)
    adapter = adapters[lora_key]
    if models[model_key]["model_name"] not in adapter["model_names"]:
        raise SystemExit(f"{lora_key} does not support {model_key}")
    path = ROOT / "models" / "loras" / lora_key / adapter["filename"]
    if not path.is_file() or not (ROOT / "models" / "loras" / lora_key / "source.json").is_file():
        raise SystemExit(f"Adapter missing. Download it with scripts/download_loras.py {lora_key}")
    scale = adapter["strength"] if strength is None else strength
    return {"key": lora_key, "path": str(path), "strength": scale, "source": adapter}


def build_prompt(model_key, width, height, seed, prompt, lora=None):
    models = model_registry(ROOT)
    model = models[model_key]
    directory = ROOT / "models" / model_key
    if is_cuda(ROOT):
        directory = ROOT / "runtime/ComfyUI/models/diffusion_models" / model["filename"]
        if not directory.is_file():
            raise SystemExit("Model missing. Run scripts/setup_cuda.py")
    elif not (directory / "source.json").is_file():
        raise SystemExit(f"Model missing. Download it with scripts/download_models.py {model_key}")
    output_key = f"{model_key}-{lora['source']['workflow_suffix']}" if lora else model_key
    return {
        "1": {
            "class_type": "Flux2KleinModelLoader",
            "inputs": {
                "model_name": model["model_name"], "quantize": model["quantize"],
                "custom_model_path": str(directory),
                "loras": (f"{lora['source']['comfy_filename']}:{lora['strength']}" if is_cuda(ROOT)
                          else json.dumps([{"path": lora["path"], "strength": lora["strength"]}])) if lora else "",
            },
        },
        "2": {
            "class_type": "Flux2KleinGenerate",
            "inputs": {
                "pipeline": ["1", 0], "prompt": prompt, "width": width, "height": height,
                "num_inference_steps": 4, "seed": seed, "guidance": 1.0,
                "filename_prefix": f"{output_key}/image", "image_strength": 0.3,
            },
        },
    }


def main():
    models = model_registry(ROOT)
    defaults_path = ROOT / "config/defaults.json"
    defaults = json.loads(defaults_path.read_text()) if defaults_path.exists() else {}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=list(models), default=defaults.get("model", "flux2-klein-9b-8bit"))
    parser.add_argument("--lora", choices=list(json.loads((ROOT / "config/loras.json").read_text())))
    parser.add_argument("--lora-strength", type=float, help="Adapter strength (default from config/loras.json)")
    parser.add_argument("--width", type=int, default=defaults.get("width", 1024))
    parser.add_argument("--height", type=int, default=defaults.get("height", 1024))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", default="generation")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    if args.lora_strength is not None and (args.lora is None or not math.isfinite(args.lora_strength)):
        parser.error("--lora-strength requires --lora and a finite value")
    if args.runs < 1 or args.timeout <= 0:
        parser.error("--runs and --timeout must be positive")
    if any(size < 64 or size > 2048 or size % 16 for size in (args.width, args.height)):
        parser.error("dimensions must be multiples of 16 between 64 and 2048")
    lora = resolve_lora(args.model, args.lora, args.lora_strength)
    workflow_key = f"{args.model}-{lora['source']['workflow_suffix']}" if lora else args.model
    stats = request(args.server, "/system_stats")
    runtime = runtime_registry(ROOT)
    patch_sha256 = None if is_cuda(ROOT) else hashlib.sha256((ROOT / "patches/flux2klein-mlx.patch").read_bytes()).hexdigest()
    lora_patch_sha256 = None if is_cuda(ROOT) else hashlib.sha256((ROOT / "patches/flux2klein-lora.patch").read_bytes()).hexdigest()
    package_names = ("gguf", "torch", "transformers") if is_cuda(ROOT) else ("mflux", "mlx", "mlx-metal", "torch", "transformers")
    packages = {name: importlib.metadata.version(name) for name in package_names}
    records = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = ROOT / "benchmarks" / f"{stamp}-{args.label}.json"
    report_path.parent.mkdir(exist_ok=True)
    for run in range(args.runs):
        seed = args.seed + run  # Always recompute; don't benchmark a cached image.
        graph = build_prompt(args.model, args.width, args.height, seed, args.prompt, lora)
        workflow = json.loads((ROOT / "workflows" / f"{workflow_key}.json").read_text())
        if lora:
            workflow["nodes"][0]["widgets_values"][3] = f"{lora['source']['comfy_filename']}:{lora['strength']}"
        workflow["nodes"][1]["widgets_values"] = [args.prompt, args.height, args.width, 4, seed, "fixed", 1.0, f"{workflow_key}/image", 0.3]
        started = time.perf_counter()
        queued = request(args.server, "/prompt", {
            "prompt": graph, "client_id": str(uuid.uuid4()),
            "extra_data": {"extra_pnginfo": {"workflow": workflow}},
        })
        if queued.get("node_errors") or "prompt_id" not in queued:
            raise RuntimeError(f"ComfyUI rejected prompt: {queued}")
        prompt_id = queued["prompt_id"]
        print(f"Run {run + 1}/{args.runs}: {args.model} {args.width}x{args.height}, seed={seed}, id={prompt_id}", flush=True)
        last_notice = 0
        while True:
            elapsed = time.perf_counter() - started
            if elapsed > args.timeout:
                request(args.server, "/interrupt", {})
                raise TimeoutError(f"Generation exceeded {args.timeout}s; interrupt requested")
            history = request(args.server, f"/history/{prompt_id}")
            if prompt_id in history:
                item = history[prompt_id]
                break
            if elapsed - last_notice >= 15:
                print(f"  Working... {elapsed:.0f}s", flush=True)
                last_notice = elapsed
            time.sleep(0.25)
        elapsed = time.perf_counter() - started
        (report_path.with_suffix(".history.json")).write_text(json.dumps(item, indent=2) + "\n")
        status = item.get("status", {})
        if status.get("status_str") != "success":
            raise RuntimeError(f"ComfyUI execution failed: {status}")
        files = []
        for node in item.get("outputs", {}).values():
            for output in node.get("images", []):
                if output.get("type") != "output":
                    continue
                path = ROOT / "outputs" / output.get("subfolder", "") / output["filename"]
                with Image.open(path) as image:
                    image.load()
                    if image.size != (args.width, args.height):
                        raise RuntimeError(f"Wrong image size: {image.size}")
                    variance = ImageStat.Stat(image.convert("RGB")).var
                    if max(variance) < 1:
                        raise RuntimeError(f"Blank image produced: {path}")
                    embedded_prompt = json.loads(image.info.get("prompt", "{}"))
                    if not embedded_prompt:
                        raise RuntimeError(f"Missing reproducibility metadata: {path}")
                    files.append({"path": str(path.relative_to(ROOT)), "size": list(image.size), "channel_variance": variance})
        if not files:
            raise RuntimeError("No saved output image found")
        messages = dict(status.get("messages", []))
        execution_seconds = None
        if "execution_start" in messages and "execution_success" in messages:
            execution_seconds = (messages["execution_success"]["timestamp"] - messages["execution_start"]["timestamp"]) / 1000
        records.append({
            "run": run + 1, "seed": seed, "prompt_id": prompt_id,
            "end_to_end_seconds": round(elapsed, 3), "server_execution_seconds": execution_seconds,
            "within_90_seconds": elapsed <= 90, "outputs": files,
        })
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(), "label": args.label,
            "model": args.model, "model_source": models[args.model] if is_cuda(ROOT) else json.loads((ROOT / "models" / args.model / "source.json").read_text()),
            "backend": "CUDA/GGUF via ComfyUI" if is_cuda(ROOT) else "MLX/Metal via ComfyUI",
            "width": args.width, "height": args.height, "steps": 4, "guidance": 1.0,
            "prompt": args.prompt, "system_stats": stats, "runs": records,
            "runtime_sources": runtime, "integration_patch_sha256": patch_sha256,
            "cuda_integration_sha256": hashlib.sha256((ROOT / "scripts/cuda_nodes.py").read_bytes()).hexdigest() if is_cuda(ROOT) else None,
            "lora_integration_patch_sha256": lora_patch_sha256, "lora": lora, "packages": packages,
            "note": "End-to-end latency includes queueing, model loading if needed, encoding, sampling, decoding and saving. Downloads excluded. Later runs change seed and recompute the image.",
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"  Completed in {elapsed:.2f}s; 90s target: {'PASS' if elapsed <= 90 else 'MISS'}", flush=True)
        for file in files:
            print(f"  Image: {file['path']}", flush=True)
    print(f"Benchmark: {report_path.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        raise SystemExit(f"Cannot reach ComfyUI. Run ./scripts/start.sh first. Details: {exc}") from exc

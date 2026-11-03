# Performance

Measured examples, not guarantees. Timings depend on the prompt, size, other running applications and
whether the engine was already awake. Times are end to end, from submitting to the saved result, with
the model already downloaded.

## Mac: Apple M5, 10-core GPU, 24 GB, macOS 26

| Task | Time |
| --- | --- |
| 1024 × 1024, Klein 9B 8-bit, 4 steps (first / second run) | 75.7 s / 64.8 s |
| 1024 × 1024, Klein 9B 4-bit | 42–46 s |
| Fast square creation, 512 × 512 | about 20 s |
| Fast square edit | about 30 s |
| Fast edit at a photo's exact size (751 × 497) | 27.4 s |
| Fast 2K creation (AI-upscaled) | 20.0 s |
| Fast 4096 × 2711 edit (AI-upscaled) | 47.0 s |
| Automatic selection with face detection (after the first run) | about 9 s |
| 2-second video, 512 × 512 | 224 s |
| 6-second video, 1001 × 667 | 569 s |

Peak GPU memory for the 8-bit model was 11.5 GB with component unloading (MLX allocator active memory,
excluding macOS and other applications). Releasing the text encoder after encoding and the transformer
before decoding took the 8-bit 1024 × 1024 image from 184 s to 76 s, with identical decoded pixels for
the same seed.

## NVIDIA: RTX 3060 Laptop, 6 GB VRAM, 16 GB RAM, Ubuntu 24.04

| Task | Time |
| --- | --- |
| High, 4096 × 4096 output | 127.5 s |
| 2-second video (49 frames, 24 fps) with audio | 339.5 s |

The GGUF builds and CUDA kernels differ from MLX, so the same seed produces a similar image, not an
identical one.

## Making it faster

- Use **Fast** while exploring; it uses the 4-bit model at a smaller size.
- For area edits, **Settings → Area edits → Working size → Quick** edits a smaller crop.
- Keep the engine awake by working in one session; the first image after 10 idle minutes reloads the
  model.
- Close other memory-heavy applications, especially on 16–24 GB machines.

Benchmark your own computer with `make benchmark`, which makes two 1024 × 1024 images and writes a
report to `benchmarks/`.

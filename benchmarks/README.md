# M5 / 24 GB validation

Measured on **5 October 2026 (Europe/Rome)**, MacBook Pro **Apple M5**,
**10-core GPU**, **24 GB unified memory**, **macOS 26.5**, connected to AC power.
Other applications remained open. Report filenames use UTC timestamps.

| Configuration at 1024 × 1024 | First image | Second image | Peak MLX allocation | 90 s target |
| --- | ---: | ---: | ---: | --- |
| **9B 8-bit with component unloading (default)** | **75.66 s** | **64.83 s** | **11.47 GB** | **Pass / pass** |
| 9B 4-bit, initial cache-limited integration | 42.26 s | 46.43 s | 13.48 GB | Pass / pass |
| 9B 8-bit, initial integration retaining components | 184.16 s | — | Not recorded | Miss |
| Experimental 8-bit transformer / 4-bit encoder, retaining components | 134.29 s | — | 14.75 GB | Miss |

The unsuccessful configurations' second runs were stopped after the first
completed measurement. The mixed preset was removed from the supported model
registry. The baseline 8-bit run overlapped the 4-bit model download; the final
default's two runs started after all model downloads completed.

All timings include the request, model loading, prompt encoding, four sampling
steps, VAE decoding, PNG saving and history polling. Download time and ComfyUI
startup are excluded. The two default runs use seeds 42 and 43, and re-create the
pipeline each time; the second run benefits from system file/kernel caches.
They do not return a cached image. These are measurements, not latency guarantees.

The default's MemorySaver uses the MFLUX callback API to release the text encoder
after encoding and the transformer before VAE decoding. The MLX allocator cache
is limited to 1 GB. The FLUX.2 VAE opts out of implicit tiling, so decoding remains
untiled. ComfyUI progress and cancellation are connected through a per-request
sampling callback.

Validation checked successful ComfyUI history status, 1024 × 1024 output, nonblank
pixel variance, embedded prompt/workflow metadata, and visual image quality.
The decoded RGB pixels at seed 42 were identical before and after component
unloading. See [the retained example](../examples/flux2-klein-9b-8bit.png).

A third generation used ComfyUI's **Run button**, a new espresso-machine prompt,
seed 123 and the portable relative model path. It completed in **59.49 s** of
server execution, produced a valid 1024 × 1024 PNG and rendered correctly in both
the Generate and Preview nodes. Details are in `comfyui-ui-verification.json`.

Peak MLX allocation is GPU allocator active memory reported by
`mx.get_peak_memory()`, rather than total system/process memory. It excludes
macOS, other applications, CPU allocations and retained allocator caches.

The JSON reports retain actual prompt IDs, prompt text, seeds, output paths,
end-to-end and server timings, and source model revisions. Full ComfyUI history
files are ignored by Git; startup logs stay under the ignored `runtime/` directory.
Software versions are pinned in `requirements.lock.txt`; ComfyUI and extension
commits are pinned in `config/runtime.json`.

## SNOFS v1.4 installation verification

On the same Mac, SNOFS v1.4 was verified with Klein 9B 8-bit at 1024 × 1024,
four steps, guidance 1.0 and adapter strength 1.0. Its 336 tensors matched all
adapter keys and applied to 144 transformer layers. The LoKr adapter runs
separately with `bake_lora=False`.

The fully clothed adult portrait completed in **193.78 s** end-to-end
(**193.52 s** server execution), with **13.36 GB** peak MLX allocation. The
result passed dimension, nonblank-pixel and embedded prompt/workflow checks,
and visual inspection. This single run exceeded the base preset's 90-second
target; it is not a controlled comparison of adapter overhead.
The 4-bit SNOFS workflow is installed but has not been benchmarked.

See [the report](20261004T234742Z-snofs-8bit.json) and the local output at
`outputs/flux2-klein-9b-8bit-snofs/image_00001_.png`.

## Beginner editing workflow

`02 - Edit Images` uses a standard ComfyUI Load Image upload connected to the
MLX editing node, Klein 9B 8-bit and SNOFS v1.4 at strength 1.0. The first
768 × 768 edit took **296.50 s** of server execution with **14.05 GB** peak
MLX allocation. End-to-end time was **394.93 s**, including a wait behind a
separate generation. Four steps, guidance 1.0 and seed 123 were used.

The output passed success-status, dimensions, nonblank pixels and embedded
Load Image/workflow checks. Visual inspection confirmed the navy coat changed
to red; the model also changed framing and details. The source file was
unchanged. See [the report](20261005T002820Z-beginner-image-edit.json),
[source](../examples/edit-source.png) and [result](../examples/edit-result.png).

A second consecutive edit was started with ComfyUI's Run button using the same
photo and seed, but an emerald-green coat instruction. It completed in
**287.25 s** with **14.05 GB** peak MLX allocation. The output passed the same
checks, displayed in ComfyUI and visibly changed the coat to green. This
confirms repeated editing works after component unloading. See
[the GUI report](20261005T003308Z-beginner-edit-ui.json).

The editing pipeline is rebuilt for each request so component unloading does
not break later edits. The plain Klein 9B workflow keeps `use_kv_cache=False`;
the optional KV-cache path is incompatible with this installed model/runtime.
The three extension patches were checked against clean pinned upstream sources,
and setup was rerun successfully without changing saved user workflows.
The launcher was checked for cold startup and reuse of the running server.

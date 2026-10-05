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
The 4-bit SNOFS workflow has not been benchmarked at 1024 × 1024.

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

## Docker website

The website was built and run in Docker Desktop, connected to the native MLX
engine through `host.docker.internal`. Two **Fast / square / SNOFS** creations
used Klein 9B 4-bit at 512 × 512 with seeds 20261005 and 20261006. Worker times
were **21.24 s** and **19.03 s**, with server execution of **20.78 s** and
**18.71 s** and **7.49 GB** peak MLX allocation.

An edit started through the website's **Edit this image** button added red
flowers to the cafe table. It used the same model/size and took **29.52 s** from
worker submission to saved result (**28.67 s** engine execution), with **8.02 GB**
peak MLX allocation. Visual inspection confirmed the flowers were added.
All three PNGs passed size, nonblank-pixel and exact embedded recipe checks,
and include ComfyUI workflows. The editing source and its SHA256 were retained.
See [the report](20261005-web-studio.json).

Favorites, Archive and Restore were exercised in the browser. Rebuilding and
restarting the Docker website preserved the library, favorites and sign-in
session. A normal sample-photo upload and its thumbnail were checked through
the API. The LAN HTTP address responded successfully. Responsive layout was
checked at 393 × 852 and 320 × 740, with no horizontal overflow; a physical
phone was not used in this verification.

`make test-studio` passes nine tests covering PIN access and rate limiting,
cross-origin rejection, recipe/search persistence, distinct variation seeds,
editing lineage, archive restoration, targeted cancellation, and recovery of
an existing engine job without submitting it twice. Fast's real GPU path was
tested through the website; Balanced and High use the existing 8-bit engine,
with the dimensions documented in [the studio guide](../studio/README.md).

## Original-size, 2K and 4K website outputs

Three real **Fast / SNOFS** jobs completed through the Docker website and native
ComfyUI engine using the pinned Real-ESRGAN Compact x4 upscaler:

| Mode / output | Generation canvas | Saved dimensions | Worker time |
| --- | --- | --- | ---: |
| Edit / Match original | 512 × 336 | 751 × 497 | 27.41 s |
| Create / 2K square | 512 × 512 | 2048 × 2048 | 20.01 s |
| Edit / 4K | 1024 × 672 | 4096 × 2711 | 46.96 s |

The edits used the same 751 × 497 cafe source. Their saved source hash stayed
unchanged. Output dimensions, nonblank pixels, exact embedded recipe metadata
and complete ComfyUI workflows with the upscaler and final Save Image node were
verified. Visual inspection confirmed the edited red flowers and clean cafe
outputs. Timings include local AI enhancement and saving, and exclude queue
waits. These outputs use upscaling after generation. See
[the report](20261005-output-sizes.json).

The automated suite now passes eleven tests, including exact odd dimensions,
source-shape preservation, 2K/4K planning, final-image selection over the smaller
base image, and consistent saved workflow links. The earlier website report
records the nine tests present at that stage.

Browser checks confirmed Match original defaults for new edits, 4K settings
reuse with the source and fixed seed, and composer restoration after reloading.
The Output size controls fit desktop, 393 × 852 and 320 × 740 viewports without
horizontal overflow. The LAN browser session reported no console errors or
warnings. See the [desktop](../examples/studio-output-sizes.png) and
[phone-sized](../examples/studio-output-sizes-mobile.png) screenshots. A physical
phone was not used.

## Website image deletion

The suite now passes fourteen tests. Permanent deletion requires an authenticated
same-origin request and an explicit confirmation. Tests with temporary data
verify removal of the PNG, thumbnail, image recipe and completed job; updated
Library/Favorites/Archive counts; preserved source photos and descendant images;
and reuse of editing settings after deleting their earlier parent image.
Restart checks cover interrupted deletion before and after the database commit.

The deployed Docker website's gallery and image details both open a confirmation
with the matching image preview. Keep image and Escape cancel it, and initial
focus goes to Keep image. The dialog fits 393 × 852 and 320 × 740 screens without
overflow; the browser reported no errors or warnings. Permanent removal was
exercised on temporary test data. User library images were preserved during UI
verification. See [the report](20261005-image-deletion.json) and the
[confirmation screenshot](../examples/studio-delete.png).

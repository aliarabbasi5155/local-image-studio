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

## Thumbnail downloads

Gallery thumbnails now have a separate download icon at the bottom left.
Chrome's Download History confirmed a completed PNG download from the 4K cafe
thumbnail. The same endpoint returned the original 9,034,399 bytes at
4096 × 2711, including the embedded recipe and workflow. Clicking the link
kept the details dialog closed. The icon also appeared in Favorites, and the
controls fit 393 × 852 and 320 × 740 viewports. `make check` passed. See
[the report](20261005-thumbnail-downloads.json) and the
[desktop screenshot](../examples/studio-thumbnail-download.png). The browser
download helpers failed to report completion; native Chrome history and the
HTTP response supplied the verification.

## Before-and-after comparisons

Saved edited images now show the full original and result in a shared viewing
area with a draggable divider and native range control. The 4K cafe edit loaded
its 751 × 497 source and 4096 × 2711 result without stretching or cropping.
Desktop dragging updated the split to 70/30; keyboard Home, End and arrow keys
showed the expected endpoints and one-percent adjustment. Reset returned to
50/50. An earlier 512 × 512 edit also worked, and an ordinary creation retained
its image viewer.

The comparison fit 393 × 852 and 320 × 740 browser viewports without horizontal
overflow. Dragging worked at phone dimensions, and the stage permits vertical
touch scrolling and pinch zoom. A physical phone was not tested. `make check`
passed, the Docker service was healthy, and the browser reported no warnings or
errors. No library images were modified. See [the report](20261005-before-after.json),
[desktop preview](../examples/studio-before-after.png) and
[phone preview](../examples/studio-before-after-mobile.png).

## JPG downloads

The thumbnail download button now offers PNG and JPG; image details have both
download buttons. An authenticated LAN request returned a valid RGB JPEG at
4096 × 2711 (1,813,361 bytes), while the saved PNG's SHA256 stayed unchanged.
JPG uses quality 95 without chroma subsampling and is encoded off the HTTP event
loop without creating another library file. PNG retains its embedded settings;
JPG sharing copies omit those settings. Transparent areas become white.

All sixteen studio tests pass, including full-size JPEG output, PNG and recipe
preservation, PIN protection, transparent-image export, and saved-source export.
`make check` also passes. The chooser fits within thumbnails at 393 × 852 and
320 × 740, supports Escape, and keeps only one menu open. Favorites and details
show both formats, and the before-and-after view remains present. No browser
console errors or warnings were reported. A physical phone was not tested.

Chrome blocked the automated JPG download with “Blocked by your organization”
and stated that the file did not meet a security policy. PNG reached the normal
Save dialog; its extra copy was canceled. No browser security settings were
changed. The JPG endpoint was verified directly, rather than claiming a
completed Chrome JPG download. See [the report](20261005-jpg-downloads.json),
[desktop preview](../examples/studio-jpg-downloads.png) and
[phone preview](../examples/studio-jpg-downloads-mobile.png).

## Collapsible bottom panel

The Create/Edit panel has Minimize and Expand controls, with its preference saved
in that browser. Desktop verification reduced the panel from 391px to an 88px
bar. A reload kept it minimized, and expanding retained the prompt, source photo,
quality, shape, output size, variation count, SNOFS and fixed seed. Ctrl/Cmd + Enter
opened a minimized panel and focused the prompt. Reuse settings and the Edit tab
also reopened it. Toasts and gallery spacing followed the panel's current height.

The compact panel measured 77px in both 393 × 852 and 320 × 740 browser viewports,
with no horizontal overflow or overlapping controls. It sat above the mobile
navigation. `make check` passed, and the browser reported no console warnings or
errors. Verification used saved cafe images without generating or changing any
library images; a physical phone was not tested. See
[the report](20261005-collapsible-panel.json),
[desktop preview](../examples/studio-collapsed-panel.png) and
[phone preview](../examples/studio-collapsed-panel-mobile.png).

## Masked edit refinement

The same 1152 × 1728 synthetic cabin, prompt and seed took 130.51 seconds with
the earlier High masked path and 110.12 seconds with the improved High path at
Balanced working resolution. The new Fast + Quick option took 27.04 seconds
of active work, plus 160.48 seconds waiting behind another engine job. This
compares different working resolutions and, for Fast, a 4-bit rather than 8-bit
model; speed comes with less fine detail. These single runs do not predict all
images or selection sizes.

Every unpainted pixel stayed identical in all three outputs. Mean RGB change
inside a three-pixel selection boundary fell from 5.842 to 0.744 for High and
0.920 for Fast. This measures a boundary seam on this fixture, not overall image
quality. Color correction applied; alignment skipped because the unchanged
context did not support a reliable improvement. Known translation drift and
requested recoloring are covered separately by pixel regression tests.

All 44 tests pass on macOS and in the Docker image with its 768MB memory limit;
`make check` passes. The painter options and saved-mask reuse were checked at
portrait and landscape phone sizes, with no console errors or warnings. See
[the report](20261006-masked-edit-refinement.json).

## Local corner color matching and automatic blending

On a synthetic textured image with a known uneven color cast, local matching
reduced inside-corner RGB error by 72.9%, 78.5% and 73.2% across three different
mask outlines, compared with the earlier whole-patch correction. The edited
center stayed unchanged. This measures the specific synthetic fixture, not
quality on the user's photo or every model output.

Automatic blending chooses a transition width for the selection and model
resolution, with narrower transitions for thin selections. It stays inside the
painted area. Manual mode retains the earlier behavior, including saved recipes
that have no blending-mode setting. Unreliable local context is skipped.

A complete 4096 × 4096 synthetic patch blend and PNG save took 1.428 seconds
in Docker with two CPUs and its existing 768MB memory limit; peak process memory
was 572.3MB. The saved recipe and unpainted comparison strip stayed identical.
No new model runs were used. All 48 tests pass on macOS and Docker; `make check`
passes. Phone-sized controls, saved-mode reuse and color-match toggles were
checked with an isolated synthetic library. See
[the report](20261006-mask-corner-blending.json).

## Automatic selections and protected faces

SAM 3 Q8 runs through a pinned sam3.cpp Metal helper, separate from the existing
Flux environment and Docker website. On Meta's public SAM 3 sample photo, initial
shirt/face detection took 16.617 seconds with a 2.02 GB peak process footprint.
Subsequent clothing, hand and arm selections with six protected faces took about
8.6–9 seconds using a 1024-pixel preview. The native process exits after inference.
No private user photos were used.

Seven real Fast Flux patch edits were tested in an isolated library. All saved
1280 × 720 PNGs preserved every unselected pixel and all 13,499 protected face
pixels exactly. A shirt recolor succeeded in 17.450 seconds. An initial flower
addition fell outside the selected box, and a rectangular guide caused a visible
box artifact. The final small destination marker placed the flower inside the
selection with clean surrounding ground in 19.272 seconds. Those earlier
approaches are not the shipped implementation. Hand/arm detection and compositing
worked, but the requested hand position was not reliably achieved; the editor
suggests including the connected arm and leaves brush correction available.
These timings apply to these small working canvases, not 4K generation.

All 56 tests pass on macOS and Docker, including source/PIN checks, protection
overlap rejection, recipe persistence, placement guidance, separate timing
samples and cancelling a generation while it waits for selection. Simultaneous
selection requests return 409; the native helper rejects requests without its
secret token. Desktop, phone portrait and phone landscape controls were checked.
See [the report](20261006-automatic-masks.json).

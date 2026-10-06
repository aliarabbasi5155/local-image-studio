# Image Studio

A small website for the existing FLUX.2 Klein / SNOFS setup. Use it on this Mac
or a phone on the same Wi-Fi. It includes image creation and editing, 1–4
variations, quality presets, exact original-size edits, 2K/4K output, Favorites,
a reversible Archive, and saved recipes.
Images can also be permanently deleted after confirmation.

## Open it again

1. Open **Docker Desktop** and wait until it is running.
2. Double-click **Start Web Studio.command** in the project folder.
3. Enter the six-digit PIN printed in the launcher. It is also saved in
   `studio-data/access-pin.txt` and stays the same after a restart.
4. On this Mac, open **http://localhost:7878**. On your phone, open the
   **On your phone** address printed by the launcher while using the same Wi-Fi.
   The **Connect** button in the website also shows that address.

You can close the launcher Terminal window and browser. Keep the Mac awake and
Docker Desktop running while making images. After restarting the Mac, launch
the studio again. If the router changes the Mac's address, the next launch
prints the new phone address. Your phone can add the page to its home screen
from the browser's Share/menu controls.

## Projects and editing

The library shows one card per **project**. Each generated image or uploaded photo
starts a project. Existing images are grouped using their saved parent and source
links; importing the old library leaves its PNGs and recipes unchanged.

- **Create:** describe an image in the bottom panel, choose quality, shape, size
  and 1–4 variations. Every variation starts its own project. **Minimize** and
  **Expand** keep the draft and give you more browsing space.
- **Upload image:** saves a PNG, JPG or WebP as **Image 0** and opens its editor.
  Editing is optional; close the viewer and the uploaded original remains saved.
- **Open a project:** the screen-filling viewer starts on Image 0. A horizontal
  thumbnail strip shows its edits in time order. Select a thumbnail, then type
  in the edit box to make a child of that particular image. Multiple edit
  variations are sibling versions in the same project. Branch labels show
  which earlier image each version came from. Drafts are remembered per image.
- **Compare:** select an edit and choose **Immediate parent** or **Original ·
  Image 0**. Drag the divider or move the slider. **Reset split** restores 50/50.
  If an earlier result was deleted, its independent saved source still supports
  parent comparison. Deleted versions disappear from the strip; later image
  numbers and parent links stay intact.
- **Zoom:** pinch with two fingers, or use **+**, **−**, and **100%**. Both images
  zoom together. Drag to pan when zoomed; drag to compare at 100%.
- **Fullscreen and controls:** the viewer fills the page immediately; its
  fullscreen button also hides browser chrome when supported. **Hide controls**
  or a tap on the photo/background hides the interface; tap again to reveal it.
  **H** toggles controls. Arrow keys move between versions when the photo is
  focused; Shift + arrow keys switch projects. The top previous/next buttons
  switch between projects in the current collection.
- **Edit settings:** choose quality, output size, resizing, variations, seed and
  SNOFS. Changes are remembered. The prompt that produced the selected version
  appears above its edit box; expand it to read the full prompt. **Reuse settings**
  copies the prompt and settings, selecting the earlier parent when it is still
  present. If that parent was deleted, the saved result becomes the editing target.
- **Favorites:** the project card's heart favorites a project. The viewer menu
  favorites a particular version, including an uploaded original. Favorites
  shows projects marked as favorites or containing a favorited image. Removing
  a project favorite clears its image favorites too.
- **Archive:** archives the whole project, retaining its original and all edits.
  Open Archive and choose **Restore project** to bring it back.
- **Delete this image:** in the viewer menu, deletes only the selected edit after
  confirmation. Its children and saved parent comparisons remain usable. To
  remove Image 0 and the whole family, use **Delete whole project** in the menu
  or the trash button on the library card. Stop that project's pending jobs
  before deleting it. Permanent deletion has no restore; Archive is reversible.
- **Download:** the arrow on a library thumbnail downloads the original as PNG
  or JPG. The viewer's download buttons save the selected version. Both retain
  the full saved dimensions. Generated PNGs include their settings and workflow;
  uploaded originals have no generation recipe. JPG is a sharing copy at quality
  95, with transparent areas flattened to white. **Download recipe** saves a
  generated version's settings separately.

## Timing and server status

The Create panel and editor show approximate duration ranges learned from recent
completed jobs on this Mac. Quality/model, create versus edit, generation pixels,
and resize/upscale processing affect the estimate. Sparse or unfamiliar settings
use a wider range; a new model/mode shows **Learning** until a run finishes.
Queued images include the studio queue and show an approximate completion time.
An image exceeding its predicted range shows **Taking longer** rather than a
misleading zero-second countdown. Other Mac activity and external ComfyUI jobs
can change the actual completion time.

Tap the status strip above the library for available Mac memory, waiting images,
last and average image times, free image storage, engine response time and website
uptime. Mac memory comes from the native image engine, not the Docker container.
The server reads these values periodically; no extra monitoring service is needed.

AI edits can also change details you asked to keep; inspect the result. The
upload accepts photos up to 20 MB and 16,777,216 pixels (including 4096 × 4096).
Convert HEIC photos to
JPEG or PNG before uploading.

## Output size

Choose **Output size** in Create or the viewer’s **Edit settings**. The line below the controls
shows the dimensions that will be saved.

The **Resize** menu lets you choose how the output is made:

- **Resize / AI upscale** keeps the existing behavior and fits the result to your selected output size.
- **No resize · native size** saves the model's output directly at the quality preset's resolution. Edits use the photo's shape. The output-size selector is disabled because no enlargement happens.
- **No resize · full size** generates directly at your selected size, without AI upscaling or a final resize. Larger images take longer and need more memory. Model dimensions use 16-pixel steps, so an odd-sized photo may save a few pixels larger or smaller.

The choice is remembered in your browser, saved in each image's recipe and restored by **Reuse settings**. To compare both no-resize options, use the same photo, prompt and fixed seed.

![Output size controls with a saved 4K editing recipe](../examples/studio-output-sizes.png)

| Choice | Saved image |
| --- | --- |
| Preset | The quality/shape dimensions in the table below |
| Match original | Exactly the editing photo's width and height, including odd dimensions |
| 2K | 2048 pixels on the longest edge |
| 4K | 4096 pixels on the longest edge |

In Edit mode, Match original, 2K and 4K keep the source photo's aspect ratio.
For example, a 751 × 497 photo saves as 751 × 497, 2048 × 1355 or 4096 × 2711.
Shape then shows **Original photo**. In Create mode, 2K/4K use the chosen shape;
a square 4K image is 4096 × 4096. Choose Preset when you want to change an edit
to a preset square, portrait or landscape canvas.

With **Resize / AI upscale**, large outputs are generated on a smaller canvas, enhanced with the local
**Real-ESRGAN Compact** AI upscaler, then fitted to the exact saved dimensions
without cropping. The generation canvas stays near one megapixel to fit this
Mac's memory. A 4K output uses at least 1024 pixels on the generation canvas's
longest edge, even with Fast. These are AI-upscaled outputs. An image's details
show both **Size** and **Generated at**; its PNG and saved recipe retain the
generation dimensions and upscaler settings. **Reuse settings** restores the
output-size choice, and **Edit this image** starts with Match original.

Match original supports up to 16,777,216 pixels and 8192 pixels per side.
Original-shape output supports aspect ratios from 1:32 to 32:1. Your saved
source remains unchanged; the AI can change details while editing or enhancing.

## Quality

| Preset | Model | Square | Portrait | Landscape |
| --- | --- | --- | --- | --- |
| Fast | Klein 9B, 4-bit | 512 × 512 | 512 × 640 | 768 × 512 |
| Balanced | Klein 9B, 8-bit | 768 × 768 | 640 × 800 | 960 × 640 |
| High | Klein 9B, 8-bit | 1024 × 1024 | 832 × 1040 | 1152 × 768 |

These dimensions apply to **Preset** output. Start with **Fast** to explore
ideas, then use Balanced or High for more detail. At larger output sizes the
generation canvas also depends on the requested dimensions, so Balanced and
High can share a canvas size. Fast uses the 4-bit model; Balanced and High use
the 8-bit model.
All presets use the distilled model's four steps and guidance 1.0. SNOFS is
enabled by default and can be switched off. The optional fixed seed makes it
easy to revisit the same settings; random seed gives a fresh starting point.

Two real Fast square creations with SNOFS took about 21 and 19 seconds on this
Mac, and a Fast square edit took about 30 seconds. Larger images and other
applications change timing. These are measured examples, not guarantees.

With Fast and SNOFS, an exact 751 × 497 edit took **27.41 seconds**, a 2K square
creation took **20.01 seconds**, and a 4096 × 2711 edit took **46.96 seconds**
from worker submission to saved result. These include AI upscaling and exclude
time waiting in the queue. See [the measured runs](../benchmarks/20261005-output-sizes.json).

## Where everything is saved

`studio-data/` is the persistent library, outside the container:

| Path | Contents |
| --- | --- |
| `studio.sqlite3` | Jobs, recipes, favorite/archive state and sign-in sessions |
| `images/` | Original generated PNGs, with embedded recipe and ComfyUI workflow |
| `uploads/` | Saved editing sources, normalized to PNG |
| `thumbs/` | Small gallery previews |
| `access-pin.txt` | Your local access PIN |

Restarting or rebuilding the container preserves this directory. To back up the
library, wait for generations to finish, stop the website with the command
below, then copy the **whole studio-data folder**. Start it again with the
launcher. Git ignores your images, PIN, sessions and model weights.

The website PIN controls access to the library and generation API. This setup
serves HTTP for your trusted local network; the PIN is not encrypted in transit.
Keep access on your local network.

Delete removes the website's copy. Previously downloaded files, backups and
ComfyUI's engine copies under `outputs/web-studio/` remain separate. Deletion
stages the website files and removes the image/job records together; after an
interrupted deletion, the next website start restores an uncommitted deletion
or finishes a committed one. It does not put deleted results back in Archive.

## Docker and developer commands

The launcher starts/reuses ComfyUI natively on the Mac, then builds and runs the
website in Docker. Docker Desktop's documented GPU support does not provide
Apple Metal GPU access to Linux containers, so the MLX engine stays native.
The container reaches it through `host.docker.internal`; the engine itself stays
on loopback port 8188. See [Docker GPU support](https://docs.docker.com/desktop/features/gpu/)
and [host networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/).
Existing model files are reused and are not copied into the Docker image.
The web launcher automatically installs the roughly 5 MB upscaler from the
[official Real-ESRGAN release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0),
checks its pinned SHA256, and makes it available to native ComfyUI. Its source
is recorded in `config/upscaler.json`; the weights stay in `models/upscale/`.
Future launches reuse the verified file without downloading it again. Upscaling
runs in native ComfyUI alongside the MLX engine.

From the project root:

```bash
# Start engine + Docker website, without opening a browser
./Start\ Web\ Studio.command --no-browser

# Stop the website (keeps the library and native engine)
docker compose --env-file runtime/studio-docker.env stop

# Inspect website logs
docker compose --env-file runtime/studio-docker.env logs --tail 100

# Run the website directly in this project's Python environment
./Start\ Web\ Studio.command --native

# API, persistence, cancellation and engine integration checks
make test-studio
make check
```

Use one website instance per library; stop Docker before switching to native
mode. Native mode uses the same data and PIN. It requires the dependencies in
`studio/requirements.txt`, already available in this checkout's `.venv`.
For a fresh checkout, install the model/runtime setup described in the root
README, download the optional 4-bit model and SNOFS, then use the web launcher.

The Docker environment is generated in `runtime/studio-docker.env`, including
the absolute host model path and your UID/GID for the data mount. Compose can
also be run manually with that file. `COMFY_URL`, `ENGINE_PROJECT_ROOT`,
`STUDIO_DATA` and `STUDIO_LAN_URL` configure the Python service.

The worker saves each ComfyUI prompt ID before submitting and recovers existing
queued/running/completed jobs after a website restart. If the engine loses a
job's history, it shows an error with **Reuse settings** rather than silently
duplicating the generation. No external services or CDNs are required by the
website. The existing model and adapter license terms still apply.

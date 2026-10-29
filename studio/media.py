"""Where saved photos live. New originals, edit sources and results are JPEG; older files stay PNG."""
import json
from pathlib import Path

SUFFIXES = (".jpg", ".png")


def media_file(directory, folder, media_id):
    """The saved file of an upload or result, in whichever format it was saved."""
    for suffix in SUFFIXES:
        path = Path(directory) / folder / (media_id + suffix)
        if path.exists():
            return path
    return Path(directory) / folder / (media_id + SUFFIXES[0])


def save_jpeg(image, path, recipe=None):
    # Quality 95 without chroma subsampling: a quarter of the PNG size and no visible loss.
    extra = {"comment": json.dumps(recipe)} if recipe is not None else {}
    image.convert("RGB").save(path, "JPEG", quality=95, subsampling=0, optimize=True, **extra)

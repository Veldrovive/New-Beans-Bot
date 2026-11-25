"""Thumbnails for the classifier page. Pillow only, no torch. Slow, so the router calls these from a thread."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

THUMB_SIDE = 320


def make_thumbnail(source: Path, thumb: Path) -> Path:
    """A WebP of the photo with its longest side at most THUMB_SIDE, made once and kept at `thumb`."""
    if thumb.exists():
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((THUMB_SIDE, THUMB_SIDE))
        partial = thumb.with_suffix(".tmp")
        image.save(partial, format="WEBP", quality=80)
    os.replace(partial, thumb)  # Two requests at once never serve half a file
    return thumb

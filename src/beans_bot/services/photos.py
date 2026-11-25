"""
Photo handling shared by the classifier service, the import script and the tests. Pillow only.

- Every stored photo is re-encoded as an RGB JPEG (quality 90) with its longest side at most 2048
  pixels, after applying its EXIF rotation. Small photos are never enlarged.
- The model looks at photos at about 224 pixels, so nothing it needs is lost, and lossless PNGs of up
  to 28 MB shrink to a few hundred KB.
- These are slow for big photos. Call them from a thread when the bot's event loop is running.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # Phones send HEIC

MAX_SIDE = 2048
JPEG_QUALITY = 90


def content_hash(data: bytes) -> str:
    """md5 of the bytes as they arrived, hex. Identifies a photo in its dataset."""
    return hashlib.md5(data).hexdigest()


def normalize_photo(data: bytes) -> bytes:
    """The photo as a JPEG no bigger than MAX_SIDE on its longest side. Raises OSError if it isn't an image."""
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)  # Only ever shrinks
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()

import hashlib
import io
from dataclasses import dataclass

import cv2
import numpy as np
import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

pillow_heif.register_heif_opener()
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}


class BadImage(ValueError):
    pass


@dataclass(frozen=True)
class Decoded:
    bgr: np.ndarray
    sha256: str

    @property
    def width(self) -> int:
        return int(self.bgr.shape[1])

    @property
    def height(self) -> int:
        return int(self.bgr.shape[0])


def _open(data: bytes) -> Image.Image:
    settings = get_settings()
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise BadImage("That file isn't a readable photo.") from e
    if image.format not in ALLOWED_FORMATS:
        raise BadImage("Use a JPG, PNG, WEBP or HEIC photo.")
    if image.width * image.height > settings.max_image_pixels:
        raise BadImage("That photo has too many pixels.")
    try:
        # Upright before detection, so face boxes line up with what the officer sees.
        return ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, ValueError, SyntaxError) as e:
        raise BadImage("That file isn't a readable photo.") from e


def decode(data: bytes) -> Decoded:
    if len(data) > get_settings().max_upload_bytes:
        raise BadImage("That photo is over 15 MB.")
    rgb = np.asarray(_open(data))
    return Decoded(
        bgr=cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), sha256=hashlib.sha256(data).hexdigest()
    )


def render_jpeg(data: bytes, max_side: int) -> bytes:
    """Upright JPEG for display. Handles HEIC, which browsers can't show."""
    image = _open(data)
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()

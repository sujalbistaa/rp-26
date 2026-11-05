"""Upload formats and orientation, using generated images."""

import hashlib
import io

import numpy as np
import pillow_heif
import pytest
from PIL import Image

from app.config import get_settings
from app.images import BadImage, decode, render_jpeg


def picture(width=64, height=32) -> Image.Image:
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    pixels[:, : width // 2] = (200, 30, 30)
    return Image.fromarray(pixels)


def encoded(image: Image.Image, fmt: str, **options) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepted_formats_decode(fmt):
    assert decode(encoded(picture(), fmt)).width == 64


def test_heic_decodes_and_renders_for_browsers():
    heif = pillow_heif.from_pillow(picture())
    out = io.BytesIO()
    heif.save(out, quality=90)
    data = out.getvalue()
    assert decode(data).width == 64
    assert render_jpeg(data, 100)[:3] == b"\xff\xd8\xff"


def test_exif_rotation_is_applied_before_detection():
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise
    decoded = decode(encoded(picture(), "JPEG", exif=exif))
    assert (decoded.width, decoded.height) == (32, 64)


def test_other_formats_are_refused():
    with pytest.raises(BadImage, match="JPG, PNG, WEBP or HEIC"):
        decode(encoded(picture(), "BMP"))


def test_garbage_is_refused():
    with pytest.raises(BadImage, match="readable"):
        decode(b"not a photo")


def test_pixel_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_pixels", 100)
    with pytest.raises(BadImage, match="too many pixels"):
        decode(encoded(picture(), "PNG"))


def test_hash_is_of_the_original_bytes():
    data = encoded(picture(), "PNG")
    assert decode(data).sha256 == hashlib.sha256(data).hexdigest()

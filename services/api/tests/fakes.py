"""Stand-ins for the models. Fixtures are coloured blocks on white, never faces.

Each block is a "face". Its colour, rounded to a coarse grid, is the identity, and the
remainder nudges the vector, so two shades of one colour act like two photos of one person.
"""

import io

import cv2
import numpy as np
from PIL import Image

from app.ml.align import TEMPLATE
from app.ml.detect import Face
from app.pipeline import ML

GRID = 64


class FakeDetector:
    def detect(self, bgr: np.ndarray) -> list[Face]:
        mask = (bgr.min(axis=2) < 200).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        faces = []
        for x, y, w, h, area in stats[1:count]:
            if area < 100:
                continue
            landmarks = TEMPLATE / 112 * [w, h] + [x, y]
            faces.append(
                Face((float(x), float(y), float(x + w), float(y + h)), 0.99, landmarks)
            )
        return sorted(faces, key=lambda f: f.box[0])


class FakeEmbedder:
    version = "fake-v1"

    def embed(self, crops: np.ndarray) -> np.ndarray:
        return np.stack([self._vector(c) for c in crops])

    def _vector(self, crop: np.ndarray) -> np.ndarray:
        colour = np.median(crop[40:72, 40:72].reshape(-1, 3), axis=0).astype(int)
        identity = int.from_bytes(bytes((colour // GRID).tolist()), "big")
        shade = int.from_bytes(bytes((colour % GRID).tolist()), "big")
        base = np.random.default_rng(identity).standard_normal(512)
        jitter = np.random.default_rng(shade).standard_normal(512)
        return (base / np.linalg.norm(base) + 0.3 * jitter / np.linalg.norm(jitter)).astype(
            np.float32
        )


fake_ml = ML(detector=FakeDetector(), embedder=FakeEmbedder())


def block_photo(
    blocks: list[tuple[int, int, int]],
    *,
    size: int = 200,
    textured: bool = True,
    seed: int = 0,
) -> bytes:
    """A PNG with one `size` px block per colour (BGR), side by side on white."""
    rng = np.random.default_rng(seed)
    canvas = np.full((size + 40, (size + 40) * max(len(blocks), 1), 3), 255, dtype=np.uint8)
    for i, colour in enumerate(blocks):
        x = 20 + i * (size + 40)
        patch = np.full((size, size, 3), colour, dtype=np.int16)
        if textured:
            patch += rng.integers(-18, 19, size=patch.shape, dtype=np.int16)
        canvas[20 : 20 + size, x : x + size] = np.clip(patch, 0, 190).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).save(out, "PNG")
    return out.getvalue()

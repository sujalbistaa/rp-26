"""The Embedder interface is the swap point for the model. See CLAUDE.md, Licensing.

Embeddings from different models can't be compared. A new Embedder needs a new `version`
and a full re-embed into the other versioned column (scripts/reembed.py).
"""

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from app.ml.runtime import load_session


class Embedder(Protocol):
    version: str

    def embed(self, crops: np.ndarray) -> np.ndarray:
        """Take (n, 112, 112, 3) BGR uint8 crops. Return (n, 512) float32, not normalised."""
        ...


class ArcFaceEmbedder:
    def __init__(self, model_path: Path, version: str) -> None:
        self.session = load_session(str(model_path))
        self.input_name = self.session.get_inputs()[0].name
        self.version = version

    def embed(self, crops: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImages(
            list(crops), 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        return self.session.run(None, {self.input_name: blob})[0]


def embed_crop(embedder: Embedder, crop: np.ndarray) -> np.ndarray:
    """Normalise each view, average the crop with its mirror, then normalise the mean."""
    vectors = embedder.embed(np.stack([crop, crop[:, ::-1]]))
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    mean = vectors.mean(axis=0)
    return (mean / np.linalg.norm(mean)).astype(np.float32)

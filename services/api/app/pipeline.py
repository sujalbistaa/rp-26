"""Detect, gate, align, embed. Shared by search, enrollment and the offline scripts."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.config import Settings, get_settings
from app.ml.align import align
from app.ml.detect import Detector, Face, ScrfdDetector
from app.ml.embed import ArcFaceEmbedder, Embedder, embed_crop
from app.ml.quality import Quality, assess


@dataclass(frozen=True)
class ML:
    detector: Detector
    embedder: Embedder


@dataclass(frozen=True)
class AnalysedFace:
    face: Face
    crop: np.ndarray
    quality: Quality

    def as_dict(self) -> dict:
        return {
            "box": [round(v, 1) for v in self.face.box],
            "score": round(self.face.score, 4),
            "landmarks": [round(float(v), 2) for v in self.face.landmarks.reshape(-1)],
            "quality": self.quality.as_dict(),
        }


@lru_cache
def get_ml() -> ML:
    s = get_settings()
    return ML(
        detector=ScrfdDetector(s.model_dir / s.detector_file, s.detect_threshold),
        embedder=ArcFaceEmbedder(s.model_dir / s.embedder_file, s.embedder_version),
    )


def analyse(bgr: np.ndarray, ml: ML, settings: Settings) -> list[AnalysedFace]:
    analysed = []
    for face in ml.detector.detect(bgr):
        crop = align(bgr, face.landmarks)
        analysed.append(AnalysedFace(face, crop, assess(face, crop, settings)))
    return analysed


def embed(item: AnalysedFace, ml: ML) -> np.ndarray:
    return embed_crop(ml.embedder, item.crop)

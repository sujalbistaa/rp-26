"""The real SCRFD and ArcFace models on synthetic fixtures. Skipped without weights or fixtures.

Fakes can't catch decoding or alignment mistakes. These are the checks that caught the
close-up bug: SCRFD finds nothing in a frame-filling face at full scale.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import REPO_ROOT, get_settings
from app.ml.detect import ScrfdDetector
from app.ml.embed import ArcFaceEmbedder
from app.pipeline import ML, analyse, embed

FIXTURES = REPO_ROOT / "data" / "fixtures" / "synthetic"


def people() -> list[Path]:
    return sorted(p for p in FIXTURES.glob("p*") if (p / "mugshot_0.jpg").exists())


pytestmark = pytest.mark.skipif(
    not (get_settings().model_dir / get_settings().embedder_file).exists() or len(people()) < 2,
    reason="needs data/models weights and scripts/make_fixtures.py output",
)


@pytest.fixture(scope="module")
def ml() -> ML:
    s = get_settings()
    return ML(
        detector=ScrfdDetector(s.model_dir / s.detector_file, s.detect_threshold),
        embedder=ArcFaceEmbedder(s.model_dir / s.embedder_file, "real"),
    )


def one_face(ml: ML, path: Path):
    faces = analyse(cv2.imread(str(path)), ml, get_settings())
    assert len(faces) >= 1, f"no face in {path}"
    return max(faces, key=lambda f: f.quality.face_px)


def test_frame_filling_close_ups_are_found(ml):
    for person in people()[:5]:
        face = one_face(ml, person / "mugshot_0.jpg")
        assert face.quality.face_px > 200


def test_embedding_is_unit_length(ml):
    vector = embed(one_face(ml, people()[0] / "mugshot_0.jpg"), ml)
    assert vector.shape == (512,)
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-4)


def test_same_person_outscores_a_different_person(ml):
    first, second = people()[:2]
    anchor = embed(one_face(ml, first / "mugshot_0.jpg"), ml)
    genuine = float(anchor @ embed(one_face(ml, first / "phone_0.jpg"), ml))
    impostor = float(anchor @ embed(one_face(ml, second / "mugshot_0.jpg"), ml))
    assert genuine > 0.6 > 0.35 > impostor


def test_heavy_blur_trips_the_quality_gate(ml):
    assert one_face(ml, people()[0] / "blur_0.jpg").quality.too_blurry is True
    assert one_face(ml, people()[0] / "mugshot_0.jpg").quality.too_blurry is False


def test_two_faces_in_one_photo_are_both_found(ml):
    a = cv2.imread(str(people()[0] / "mugshot_0.jpg"))
    b = cv2.imread(str(people()[1] / "mugshot_0.jpg"))
    faces = analyse(np.hstack([a, b]), ml, get_settings())
    assert len(faces) >= 2
    assert faces[0].face.box[0] < faces[1].face.box[0]

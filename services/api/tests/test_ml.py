import cv2
import numpy as np
import pytest

from app.config import Settings, get_settings
from app.db.models import Band, Calibration
from app.ml.align import CROP_SIZE, TEMPLATE, align
from app.ml.calibrate import MAX_CONFIDENCE, Curve, degeneracy, fit, threshold_at_far
from app.ml.detect import Face, _nms
from app.ml.embed import embed_crop
from app.ml.quality import assess, estimate_yaw
from tests.fakes import FakeEmbedder


def impostors(n=200_000, seed=0):
    return np.random.default_rng(seed).normal(0.05, 0.09, n)


def genuines(n=5_000, seed=1):
    return np.random.default_rng(seed).normal(0.62, 0.12, n)


@pytest.mark.parametrize("far", [1e-2, 1e-3, 1e-4])
def test_threshold_meets_the_far_target(far):
    scores = impostors()
    threshold = threshold_at_far(scores, far)
    assert (scores >= threshold).mean() <= far


def test_threshold_is_tight():
    scores = impostors()
    threshold = threshold_at_far(scores, 1e-3)
    assert (scores >= threshold - 1e-3).mean() > 0.0009


def test_bands_are_ordered():
    result = fit(genuines(), impostors())
    assert result.floor_cos <= result.possible_cos <= result.strong_cos


def test_curve_is_monotonic_and_bounded():
    result = fit(genuines(), impostors())
    assert all(a <= b for a, b in zip(result.conf_knots, result.conf_knots[1:], strict=False))
    assert 0 <= min(result.conf_knots) and max(result.conf_knots) <= 1


def row_from(result) -> Calibration:
    return Calibration(
        version=1,
        embedder_version="x",
        cos_knots=result.cos_knots,
        conf_knots=result.conf_knots,
        floor_cos=result.floor_cos,
        possible_cos=result.possible_cos,
        strong_cos=result.strong_cos,
        synthetic=False,
    )


def test_curve_assigns_bands_at_the_stored_thresholds():
    result = fit(genuines(), impostors())
    curve = Curve(row_from(result))
    assert curve.band(result.strong_cos) == Band.STRONG
    assert curve.band(result.strong_cos - 1e-6) == Band.POSSIBLE
    assert curve.band(result.possible_cos - 1e-6) == Band.WEAK
    assert curve.band(result.floor_cos - 1e-6) is None


def test_override_can_only_raise_the_floor():
    result = fit(genuines(), impostors())
    row = row_from(result)
    row.floor_override_cos = result.floor_cos - 0.2
    assert Curve(row).floor == result.floor_cos
    row.floor_override_cos = result.floor_cos + 0.1
    assert Curve(row).floor == result.floor_cos + 0.1


def test_confidence_never_claims_certainty():
    curve = Curve(row_from(fit(genuines(), impostors())))
    assert curve.confidence(1.0) == MAX_CONFIDENCE


def test_confidence_is_not_the_cosine():
    curve = Curve(row_from(fit(genuines(), impostors())))
    assert curve.confidence(0.6) != pytest.approx(0.6, abs=0.05)


def test_embedding_is_unit_length():
    crop = np.full((112, 112, 3), 100, dtype=np.uint8)
    assert np.linalg.norm(embed_crop(FakeEmbedder(), crop)) == pytest.approx(1.0, abs=1e-5)


def test_flip_is_embedded_and_averaged():
    class Recording:
        version = "r"
        seen = None

        def embed(self, crops):
            Recording.seen = crops
            left = np.zeros(512, dtype=np.float32)
            right = np.zeros(512, dtype=np.float32)
            left[0], right[1] = 3.0, 5.0
            return np.stack([left, right])

    crop = np.arange(112 * 112 * 3, dtype=np.uint32).reshape(112, 112, 3).astype(np.uint8)
    vector = embed_crop(Recording(), crop)
    assert np.array_equal(Recording.seen[1], crop[:, ::-1])
    # Each view is normalised first, so neither dominates the mean.
    assert vector[0] == pytest.approx(vector[1])
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)


def test_align_maps_landmarks_onto_the_template():
    canvas = np.zeros((400, 400, 3), dtype=np.uint8)
    points = TEMPLATE * 2 + 60
    for x, y in points:
        cv2.circle(canvas, (int(x), int(y)), 4, (255, 255, 255), -1)
    crop = align(canvas, points)
    assert crop.shape == (CROP_SIZE, CROP_SIZE, 3)
    for x, y in TEMPLATE:
        assert crop[int(y), int(x)].max() > 200


def test_yaw_reads_zero_frontal_and_large_when_turned():
    frontal = TEMPLATE.copy()
    assert abs(estimate_yaw(frontal)) < 5
    turned = TEMPLATE.copy()
    turned[2, 0] = turned[1, 0] - 2
    assert abs(estimate_yaw(turned)) > 45


def test_yaw_ignores_head_roll():
    angle = np.radians(30)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]], dtype=np.float32
    )
    assert abs(estimate_yaw(TEMPLATE @ rotation.T)) < 5


def face_of(size: int) -> Face:
    return Face((0.0, 0.0, float(size), float(size)), 0.9, TEMPLATE.copy())


def test_quality_gate_flags_blur_and_size():
    settings: Settings = get_settings()
    sharp = np.random.default_rng(0).integers(0, 255, (112, 112, 3), dtype=np.uint8)
    flat = np.full((112, 112, 3), 128, dtype=np.uint8)
    assert assess(face_of(200), sharp, settings).low is False
    assert assess(face_of(200), flat, settings).too_blurry is True
    assert assess(face_of(79), sharp, settings).too_small is True
    assert assess(face_of(80), sharp, settings).too_small is False


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.5, 0.9, 0.7], dtype=np.float32)
    assert sorted(_nms(boxes, scores, 0.4)) == [1, 2]


def test_separated_scores_are_flagged_as_degenerate():
    """Scores that never overlap fit a step, which would show STRONG at 0% confidence."""
    easy = fit(np.random.default_rng(1).normal(0.9, 0.02, 2000), impostors())
    assert "too easy" in degeneracy(easy)


def test_overlapping_scores_are_not_degenerate():
    assert degeneracy(fit(genuines(), impostors())) is None

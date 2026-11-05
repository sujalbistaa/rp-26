"""Turns raw cosine into a calibrated confidence and the three band thresholds.

Bands are per search. A false-match rate per comparison multiplies by gallery size, so
thresholds come from FPIR: search people who aren't in the gallery, keep each top score, and
set a threshold that only a target share of those searches clear. FNIR is the share of true
matches a threshold misses. Both only hold for a gallery of about the calibrated size.

Confidence is P(same person | cosine) from an isotonic regression, with genuine and impostor
pairs weighted equally. It's a score on a fixed 50/50 prior, not the chance that a given
search result is the right person, so officers never see it. It stays in the audit trail.
"""

import math
from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression

from app.db.models import Band, Calibration

STRONG_FAR = 1e-4
POSSIBLE_FAR = 1e-3
FLOOR_FAR = 1e-2
STRONG_FPIR = 1e-3
POSSIBLE_FPIR = 1e-2
FLOOR_FPIR = 1e-1
# Zero events in n trials puts the 95% upper bound at about 3 / n, so a target needs 3 / target.
RULE_OF_THREE = 3
MAX_GALLERY_DRIFT = 2.0
# 1e-4 can't be estimated from fewer than ~10 / FAR impostor pairs.
MIN_IMPOSTOR_PAIRS = 100_000
MIN_GENUINE_PAIRS = 500
MIN_CONFIDENCE_AT_STRONG = 0.5
# Isotonic reaches 1.0 past the last impostor pair. No face match is ever certain.
MAX_CONFIDENCE = 0.999


@dataclass(frozen=True)
class Fit:
    cos_knots: list[float]
    conf_knots: list[float]
    floor_cos: float
    possible_cos: float
    strong_cos: float
    n_genuine: int
    n_impostor: int


def threshold_at_far(impostor: np.ndarray, far: float) -> float:
    """Lowest cosine where the share of impostor pairs at or above it is <= far."""
    ranked = np.sort(impostor)[::-1]
    allowed = int(np.floor(far * ranked.size))
    if allowed >= ranked.size:
        return float(ranked[-1])
    return float(np.nextafter(ranked[allowed], np.inf))


def fit(genuine: np.ndarray, impostor: np.ndarray) -> Fit:
    x = np.concatenate([genuine, impostor]).astype(np.float64)
    y = np.concatenate([np.ones(genuine.size), np.zeros(impostor.size)])
    weights = np.concatenate(
        [np.full(genuine.size, 0.5 / genuine.size), np.full(impostor.size, 0.5 / impostor.size)]
    )
    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    model.fit(x, y, sample_weight=weights)

    floor = threshold_at_far(impostor, FLOOR_FAR)
    possible = threshold_at_far(impostor, POSSIBLE_FAR)
    strong = threshold_at_far(impostor, STRONG_FAR)
    return Fit(
        cos_knots=[float(v) for v in model.X_thresholds_],
        conf_knots=[float(v) for v in model.y_thresholds_],
        floor_cos=floor,
        possible_cos=max(possible, floor),
        strong_cos=max(strong, possible, floor),
        n_genuine=int(genuine.size),
        n_impostor=int(impostor.size),
    )


def min_searches(target: float) -> int:
    return math.ceil(RULE_OF_THREE / target)


def fnir(mated: np.ndarray, threshold: float) -> float:
    return float((mated < threshold).mean())


def fpir(nonmated: np.ndarray, threshold: float) -> float:
    return float((nonmated >= threshold).mean())


def fit_fpir(
    result: Fit, nonmated: np.ndarray, mated: np.ndarray, gallery_size: int
) -> tuple[Fit, dict]:
    """Replace a pair-based fit's thresholds with per-search ones. Refuses unsupported targets."""
    short = [t for t in (STRONG_FPIR, POSSIBLE_FPIR, FLOOR_FPIR) if nonmated.size < min_searches(t)]
    if short:
        raise ValueError(
            f"{nonmated.size} non-mated searches can't support FPIR {min(short):g}: "
            f"it needs {min_searches(min(short))}"
        )
    floor = threshold_at_far(nonmated, FLOOR_FPIR)
    possible = max(threshold_at_far(nonmated, POSSIBLE_FPIR), floor)
    strong = max(threshold_at_far(nonmated, STRONG_FPIR), possible)
    report = {
        "gallery_size": gallery_size,
        "nonmated_searches": int(nonmated.size),
        "mated_searches": int(mated.size),
        "bands": {
            name: {
                "target_fpir": target,
                "cosine": cosine,
                "measured_fpir": fpir(nonmated, cosine),
                "fnir": fnir(mated, cosine),
            }
            for name, target, cosine in (
                ("STRONG", STRONG_FPIR, strong),
                ("POSSIBLE", POSSIBLE_FPIR, possible),
                ("FLOOR", FLOOR_FPIR, floor),
            )
        },
    }
    fitted = Fit(
        cos_knots=result.cos_knots,
        conf_knots=result.conf_knots,
        floor_cos=floor,
        possible_cos=possible,
        strong_cos=strong,
        n_genuine=result.n_genuine,
        n_impostor=result.n_impostor,
    )
    return fitted, report


def top_scores(
    vectors: np.ndarray, people: np.ndarray, gallery_size: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate searches on labelled photos, leaving each probe's own person out or in.

    Non-mated: top score against a gallery of `gallery_size` images of other people.
    Mated: the best score against the same person's other photos, the score a search needs
    to clear to surface the true match.
    """
    scores = vectors @ vectors.T
    nonmated, mated = [], []
    for i in range(len(vectors)):
        others = np.flatnonzero(people != people[i])
        picked = rng.choice(others, min(gallery_size, others.size), replace=False)
        nonmated.append(scores[i, picked].max())
        same = np.flatnonzero(people == people[i])
        same = same[same != i]
        if same.size:
            mated.append(scores[i, same].max())
    return np.array(nonmated), np.array(mated)


def gallery_drifted(calibrated: int | None, current: int | None) -> bool:
    """True when the gallery has moved more than 2x from the size the bands were set on."""
    if not calibrated or not current:
        return False
    return max(calibrated, current) / min(calibrated, current) > MAX_GALLERY_DRIFT


def degeneracy(result: Fit) -> str | None:
    """Why a fit shouldn't go live, or None.

    Genuine and impostor scores that never overlap fit a step, not a curve. Bands still come
    out, but a result can then read STRONG at 0% confidence.
    """
    knots_x, knots_y = np.asarray(result.cos_knots), np.asarray(result.conf_knots)
    at_strong = float(np.interp(result.strong_cos, knots_x, knots_y))
    if at_strong < MIN_CONFIDENCE_AT_STRONG:
        return (
            f"confidence at the STRONG threshold is {at_strong:.0%}. Genuine and impostor "
            "scores barely overlap, so these pairs are too easy to calibrate on"
        )
    return None


class Curve:
    """A stored calibration row, ready to score results."""

    def __init__(self, row: Calibration) -> None:
        self.version = row.version
        self.synthetic = row.synthetic
        self.embedder_version = row.embedder_version
        self._x = np.asarray(row.cos_knots)
        self._y = np.asarray(row.conf_knots)
        self.strong = row.strong_cos
        self.possible = row.possible_cos
        self.floor = max(row.floor_cos, row.floor_override_cos or row.floor_cos)

    def confidence(self, cosine: float) -> float:
        return min(float(np.interp(cosine, self._x, self._y)), MAX_CONFIDENCE)

    def band(self, cosine: float) -> Band | None:
        """None means below the floor, and the result is not shown."""
        if cosine < self.floor:
            return None
        if cosine >= self.strong:
            return Band.STRONG
        if cosine >= self.possible:
            return Band.POSSIBLE
        return Band.WEAK

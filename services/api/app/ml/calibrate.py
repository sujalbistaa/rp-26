"""Turns raw cosine into a calibrated confidence and the three band thresholds.

Confidence is P(same person | cosine) from an isotonic regression, with genuine and impostor
pairs weighted equally. It's a score on a fixed 50/50 prior, not the chance that a given
search result is the right person: that also depends on gallery size, which is why the bands
are set from false-match rate and not from confidence.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression

from app.db.models import Band, Calibration

STRONG_FAR = 1e-4
POSSIBLE_FAR = 1e-3
FLOOR_FAR = 1e-2
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

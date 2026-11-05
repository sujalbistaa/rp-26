"""Fit the confidence curve and band thresholds from labelled pairs, and store a new version.

    uv run python scripts/calibrate.py --pairs data/eval/pairs.csv

The pairs file has columns a, b, same. Paths are relative to data/. Pairs must come from our
own gallery: a curve fitted elsewhere says nothing about our cameras or our people.
"""

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

from _paths import ROOT

from sqlalchemy import func, select, update  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Calibration, PersonImage  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.ml.calibrate import (  # noqa: E402
    MIN_GENUINE_PAIRS,
    MIN_IMPOSTOR_PAIRS,
    degeneracy,
    fit,
    fit_fpir,
    top_scores,
)
from app.pipeline import ML, analyse, embed, get_ml  # noqa: E402


def load_pairs(path: Path) -> list[tuple[str, str, int]]:
    with path.open(newline="") as handle:
        return [(r["a"], r["b"], int(r["same"])) for r in csv.DictReader(handle)]


def embed_files(names: set[str], ml: ML) -> dict[str, np.ndarray]:
    """Embed each photo once. A photo without exactly one face is left out and reported."""
    settings = get_settings()
    vectors: dict[str, np.ndarray] = {}
    skipped = 0
    for i, name in enumerate(sorted(names), start=1):
        image = cv2.imread(str(ROOT / "data" / name))
        faces = analyse(image, ml, settings) if image is not None else []
        if len(faces) == 1:
            vectors[name] = embed(faces[0], ml)
        else:
            skipped += 1
        if i % 100 == 0:
            print(f"embedded {i}/{len(names)}", flush=True)
    if skipped:
        print(f"skipped {skipped} photos without exactly one face", file=sys.stderr)
    return vectors


def score_pairs(
    pairs: list[tuple[str, str, int]], vectors: dict[str, np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    genuine, impostor = [], []
    for a, b, same in pairs:
        if a in vectors and b in vectors:
            (genuine if same else impostor).append(float(vectors[a] @ vectors[b]))
    return np.array(genuine), np.array(impostor)


def current_gallery_size() -> int:
    column = getattr(PersonImage, get_settings().embedding_column)
    with new_session() as db:
        return db.scalar(select(func.count()).where(column.is_not(None))) or 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=ROOT / "data" / "eval" / "pairs.csv")
    parser.add_argument("--note", default=None)
    parser.add_argument("--synthetic", action="store_true", help="pairs are synthetic fixtures")
    parser.add_argument("--activate", action="store_true", help="make this version live")
    parser.add_argument("--allow-small", action="store_true", help="fit below the pair minimums")
    parser.add_argument("--gallery-size", type=int, help="images in the gallery. Default: the DB")
    args = parser.parse_args()

    pairs = load_pairs(args.pairs)
    ml = get_ml()
    vectors = embed_files({p for a, b, _ in pairs for p in (a, b)}, ml)
    genuine, impostor = score_pairs(pairs, vectors)
    print(f"{genuine.size} genuine pairs, {impostor.size} impostor pairs")
    if genuine.size < MIN_GENUINE_PAIRS or impostor.size < MIN_IMPOSTOR_PAIRS:
        message = (
            f"need {MIN_GENUINE_PAIRS} genuine and {MIN_IMPOSTOR_PAIRS} impostor pairs "
            "to estimate a false-match rate of 1 in 10,000"
        )
        if not args.allow_small:
            sys.exit(message)
        print(f"warning: {message}", file=sys.stderr)

    gallery = args.gallery_size or current_gallery_size()
    ordered = sorted(vectors)
    people = np.array([Path(name).parent.name for name in ordered])
    matrix = np.stack([vectors[name] for name in ordered])
    nonmated, mated = top_scores(matrix, people, gallery, np.random.default_rng(1))
    result, report = fit(genuine, impostor), None
    try:
        result, report = fit_fpir(result, nonmated, mated, gallery)
    except ValueError as e:
        if not args.allow_small:
            sys.exit(str(e))
        print(f"warning: {e}. Keeping per-comparison bands", file=sys.stderr)
    if report:
        print(f"gallery {gallery}, {report['nonmated_searches']} non-mated searches, "
              f"{report['mated_searches']} mated")
        for band, row in report["bands"].items():
            print(f"  {band:<9} cosine {row['cosine']:.4f}  FPIR {row['measured_fpir']:.4%}  "
                  f"FNIR {row['fnir']:.2%}")
    problem = degeneracy(result)
    if problem:
        sys.exit(f"refusing to store this fit: {problem}")
    with new_session() as db:
        if args.activate:
            db.execute(update(Calibration).where(Calibration.active).values(active=False))
        row = Calibration(
            embedder_version=ml.embedder.version,
            cos_knots=result.cos_knots,
            conf_knots=result.conf_knots,
            floor_cos=result.floor_cos,
            possible_cos=result.possible_cos,
            strong_cos=result.strong_cos,
            n_genuine=result.n_genuine,
            n_impostor=result.n_impostor,
            gallery_size=gallery if report else None,
            fpir_report=report,
            synthetic=args.synthetic,
            active=args.activate,
            note=args.note,
        )
        db.add(row)
        db.commit()
        print(f"calibration v{row.version} {'active' if args.activate else 'stored, not active'}")
    print(
        f"cosine thresholds: floor {result.floor_cos:.4f}, "
        f"possible {result.possible_cos:.4f}, strong {result.strong_cos:.4f}"
    )
    print(f"genuine cosine p1/p50: {np.percentile(genuine, 1):.3f} / {np.median(genuine):.3f}")
    print(f"impostor cosine p50/p99.99: {np.median(impostor):.3f} / "
          f"{np.percentile(impostor, 99.99):.3f}")


if __name__ == "__main__":
    main()

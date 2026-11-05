"""TAR at FAR = 1e-3 and 1e-4 on the held-out set, overall and by slice.

    uv run python scripts/eval.py
    uv run python scripts/eval.py --baseline data/eval/baseline.json

Exits 1 if any slice is more than 2 points below the baseline. That change can't ship
without sign-off.
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from _paths import ROOT

from calibrate import embed_files  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db.models import Calibration  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.ml.calibrate import threshold_at_far  # noqa: E402
from app.pipeline import get_ml  # noqa: E402

FARS = (1e-3, 1e-4)
MAX_REGRESSION_POINTS = 2.0
SLICE_COLUMNS = ("source", "gender", "age_band")


def tar(genuine: np.ndarray, impostor: np.ndarray, far: float) -> float | None:
    """None when there are too few impostor pairs to resolve this FAR."""
    if genuine.size == 0 or impostor.size < 1 / far:
        return None
    return float((genuine >= threshold_at_far(impostor, far)).mean())


def band_of(score: float, row: Calibration) -> str:
    if score >= row.strong_cos:
        return "STRONG"
    if score >= row.possible_cos:
        return "POSSIBLE"
    return "WEAK" if score >= row.floor_cos else "NO MATCH"


def per_search(probes: dict[str, np.ndarray], gallery_pairs: Path, ml) -> dict:
    """Run held-out people through the active bands, against a gallery they aren't in.

    Each held-out person is then enrolled with their other photos, to count true matches.
    """
    with gallery_pairs.open(newline="") as handle:
        names = {r[c] for r in csv.DictReader(handle) for c in ("a", "b")}
    gallery = embed_files(names, ml)
    matrix = np.stack(list(gallery.values()))
    with new_session() as db:
        row = db.scalar(select(Calibration).where(Calibration.active))
    if row is None:
        sys.exit("no active calibration")

    def person(name: str) -> str:
        return Path(name).parent.name

    nonmated: dict[str, int] = defaultdict(int)
    mated: dict[str, int] = defaultdict(int)
    top_one = mated_total = 0
    for name, vector in probes.items():
        impostor = float((matrix @ vector).max())
        nonmated[band_of(impostor, row)] += 1
        same = [v for n, v in probes.items() if n != name and person(n) == person(name)]
        if same:
            true_score = float((np.stack(same) @ vector).max())
            mated_total += 1
            mated[band_of(true_score, row)] += 1
            top_one += true_score > impostor
    out = {
        "calibration_version": row.version,
        "gallery_images": int(matrix.shape[0]),
        "gallery_people": len({person(n) for n in gallery}),
        "nonmated_searches": len(probes),
        "nonmated_bands": dict(nonmated),
        "mated_searches": mated_total,
        "mated_bands": dict(mated),
        "mated_rank1": top_one / max(mated_total, 1),
    }
    print("\nper-search report on held-out people")
    print(json.dumps(out, indent=2))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=ROOT / "data" / "eval" / "heldout.csv")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "eval" / "report.json")
    parser.add_argument("--gallery-size", type=int, default=18_204)
    parser.add_argument(
        "--gallery-pairs",
        type=Path,
        help="pairs file whose photos form the gallery. Adds the per-search report.",
    )
    args = parser.parse_args()

    with args.pairs.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    ml = get_ml()
    vectors = embed_files({r[c] for r in rows for c in ("a", "b")}, ml)

    genuine: dict[str, list[float]] = defaultdict(list)
    impostor: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["a"] not in vectors or row["b"] not in vectors:
            continue
        score = float(vectors[row["a"]] @ vectors[row["b"]])
        target = genuine if int(row["same"]) else impostor
        target["overall"].append(score)
        for column in SLICE_COLUMNS:
            if row.get(column):
                target[f"{column}={row[column]}"].append(score)

    # Slices are scored against every impostor pair, so each one has FAR resolution.
    all_impostors = np.array(impostor["overall"])
    report: dict[str, dict] = {}
    print(f"{'SLICE':<24}{'GENUINE':>9}{'TAR@1e-3':>11}{'TAR@1e-4':>11}")
    for name in sorted(genuine, key=lambda n: (n != "overall", n)):
        scores = np.array(genuine[name])
        values = {str(far): tar(scores, all_impostors, far) for far in FARS}
        report[name] = {"genuine_pairs": int(scores.size), "tar": values}
        cells = [f"{100 * v:.2f}%" if v is not None else "n/a" for v in values.values()]
        print(f"{name:<24}{scores.size:>9}{cells[0]:>11}{cells[1]:>11}")

    # A false-match rate is per comparison. A search makes one comparison per gallery image.
    print(f"\nimpostor pairs: {all_impostors.size}")
    print(f"expected false matches per search, gallery of {args.gallery_size:,} images:")
    for far in FARS:
        print(f"  at FAR {far:g}: {far * args.gallery_size:.2f}")

    if args.gallery_pairs:
        report["per_search"] = per_search(vectors, args.gallery_pairs, ml)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(f"\nreport: {args.out}")

    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        failed = []
        for name, entry in baseline.items():
            for far, before in entry["tar"].items():
                after = report.get(name, {}).get("tar", {}).get(far)
                if before is None or after is None:
                    continue
                drop = 100 * (before - after)
                if drop > MAX_REGRESSION_POINTS:
                    failed.append(f"{name} @ {far}: down {drop:.2f} points")
        if failed:
            print("REGRESSION, needs sign-off:\n  " + "\n  ".join(failed))
            sys.exit(1)
        print("no slice regressed by more than 2 points")


if __name__ == "__main__":
    main()

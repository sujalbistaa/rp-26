"""Accuracy on a folder of real photos, one sub-folder per person. Aggregates only.

    uv run python scripts/eval_real.py --root /path/to/people --out docs/results/real_eval.md

Prints and stores totals, never a filename or a person. The photos stay where they are and
nothing is enrolled, so this can't put anyone in the gallery.
"""

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from _paths import ROOT

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Calibration  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.ml.calibrate import threshold_at_far  # noqa: E402
from app.pipeline import analyse, embed, get_ml  # noqa: E402


def wilson(hits: int, n: int) -> tuple[float, float]:
    """95% interval for a proportion. Honest at small n, unlike +/- 2 standard errors."""
    if n == 0:
        return 0.0, 0.0
    z, p = 1.96, hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def pct(hits: int, n: int) -> str:
    low, high = wilson(hits, n)
    return f"{100 * hits / max(n, 1):.1f}% ({hits}/{n}, 95% CI {100 * low:.1f}-{100 * high:.1f})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "results" / "real_eval.md")
    parser.add_argument("--label", default="real photos")
    args = parser.parse_args()

    settings, ml = get_settings(), get_ml()
    vectors, people, views = [], [], []
    faces_found: dict[str, int] = defaultdict(int)
    per_view: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    folders = sorted(p for p in args.root.iterdir() if p.is_dir())
    for number, folder in enumerate(folders):
        for path in sorted(folder.glob("*.jpg")):
            view = path.stem.rsplit("_", 1)[-1]
            per_view[view][1] += 1
            image = cv2.imread(str(path))
            faces = analyse(image, ml, settings) if image is not None else []
            faces_found["none" if not faces else "one" if len(faces) == 1 else "several"] += 1
            if len(faces) != 1:
                continue
            per_view[view][0] += 1
            vectors.append(embed(faces[0], ml))
            people.append(number)
            views.append(view)

    matrix, people, views = np.stack(vectors), np.array(people), np.array(views)
    scores = matrix @ matrix.T
    upper = np.triu_indices(len(matrix), k=1)
    same = people[upper[0]] == people[upper[1]]
    genuine, impostor = scores[upper][same], scores[upper][~same]

    lines = [
        f"# Accuracy on {args.label}",
        "",
        f"People: {len(folders)}. Photos: {sum(v[1] for v in per_view.values())}. "
        f"Model: {settings.embedder_version}. Totals only, no names or images.",
        "",
        "## Face detection",
        "",
        f"- Exactly one face: {faces_found['one']}, none: {faces_found['none']}, "
        f"several: {faces_found['several']}",
    ]
    lines += [f"- View `{k}`: {pct(*v)} usable" for k, v in sorted(per_view.items())]

    lines += ["", "## Verification (1:1)", "", f"- Genuine pairs {genuine.size}, impostor pairs {impostor.size}"]
    lines.append(
        f"- Cosine, genuine median {np.median(genuine):.3f}, impostor median "
        f"{np.median(impostor):.3f}, impostor max {impostor.max():.3f}"
    )
    for far in (1e-2, 1e-3):
        if impostor.size >= 10 / far:
            threshold = threshold_at_far(impostor, far)
            lines.append(f"- TAR at FAR {far:g}: {pct(int((genuine >= threshold).sum()), genuine.size)}")
        else:
            lines.append(f"- TAR at FAR {far:g}: not reported, too few impostor pairs")
    lines.append("- TAR at FAR 1e-4: not reported, needs at least 100,000 impostor pairs")

    # Each photo searches a gallery of everyone's other photos.
    hits = total = 0
    by_probe: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for i in range(len(matrix)):
        row = scores[i].copy()
        row[i] = -2
        if (people == people[i]).sum() < 2:
            continue
        correct = int(people[int(row.argmax())] == people[i])
        hits += correct
        total += 1
        by_probe[views[i]][0] += correct
        by_probe[views[i]][1] += 1
    lines += ["", "## Identification (1:N, leave one photo out)", "", f"- Rank-1: {pct(hits, total)}"]
    lines += [f"- Rank-1, probe view `{k}`: {pct(*v)}" for k, v in sorted(by_probe.items())]

    with new_session() as db:
        row = db.scalar(select(Calibration).where(Calibration.active))
    if row is not None:
        def band(score: float) -> str:
            if score >= row.strong_cos:
                return "STRONG"
            if score >= row.possible_cos:
                return "POSSIBLE"
            return "WEAK" if score >= row.floor_cos else "NO MATCH"

        mated: dict[str, int] = defaultdict(int)
        nonmated: dict[str, int] = defaultdict(int)
        for i in range(len(matrix)):
            same = people == people[i]
            others = same.copy()
            others[i] = False
            if others.any():
                mated[band(float(scores[i][others].max()))] += 1
            nonmated[band(float(scores[i][~same].max()))] += 1
        lines += [
            "",
            f"## Active calibration v{row.version} applied to these photos",
            "",
            f"Synthetic: {row.synthetic}. Thresholds were set on "
            f"{'synthetic faces' if row.synthetic else 'real data'}, and this gallery "
            f"({len(matrix)} photos) is smaller than the calibrated one ({row.gallery_size}).",
            "",
            "| Band of the top score | True person enrolled | Person not enrolled |",
            "|---|---|---|",
        ]
        for name in ("STRONG", "POSSIBLE", "WEAK", "NO MATCH"):
            lines.append(f"| {name} | {pct(mated[name], sum(mated.values()))} | "
                         f"{pct(nonmated[name], sum(nonmated.values()))} |")

    report = "\n".join(lines) + "\n"
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)


if __name__ == "__main__":
    sys.exit(main())

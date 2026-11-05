"""Benchmark candidate embedders on the same detected, aligned faces.

    uv run python scripts/compare_models.py --real /path/to/people

Detection and alignment run once per photo, so only the embedder differs. Synthetic numbers
come from the held-out fixtures. --real adds a folder of people, reported as totals only.
Results go to docs/results/models.md.
"""

import argparse
import csv
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from _paths import ROOT

from app.config import get_settings  # noqa: E402
from app.ml.align import align  # noqa: E402
from app.ml.calibrate import threshold_at_far  # noqa: E402
from app.ml.embed import ArcFaceEmbedder, SFaceEmbedder, embed_crop  # noqa: E402
from app.pipeline import get_ml  # noqa: E402

MODELS = ROOT / "data" / "models"
OUT = ROOT / "docs" / "results" / "models.md"
CANDIDATES = {
    "buffalo_l w600k_r50": (lambda: ArcFaceEmbedder(MODELS / "w600k_r50.onnx", "arcface-w600k-r50"), "Non-commercial research"),
    "antelopev2 glintr100": (lambda: ArcFaceEmbedder(MODELS / "glintr100.onnx", "arcface-glintr100"), "Non-commercial research"),
    "OpenCV Zoo SFace": (lambda: SFaceEmbedder(MODELS / "face_recognition_sface_2021dec.onnx", "sface-2021dec"), "Apache-2.0"),
}


def vram() -> int:
    out = subprocess.run(  # noqa: S603
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.split()[0])


def crops_for(paths: list[Path], ml) -> dict[Path, np.ndarray]:
    crops = {}
    for path in paths:
        image = cv2.imread(str(path))
        faces = ml.detector.detect(image) if image is not None else []
        if len(faces) == 1:
            crops[path] = align(image, faces[0].landmarks)
    return crops


def tar(genuine: np.ndarray, impostor: np.ndarray, far: float) -> str:
    if genuine.size == 0 or impostor.size < 10 / far:
        return "n/a"
    return f"{100 * float((genuine >= threshold_at_far(impostor, far)).mean()):.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, default=ROOT / "data" / "eval" / "heldout.csv")
    parser.add_argument("--real", type=Path)
    args = parser.parse_args()

    ml = get_ml()
    with args.pairs.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    names = sorted({r[c] for r in rows for c in ("a", "b")})
    synthetic = crops_for([ROOT / "data" / n for n in names], ml)
    print(f"{len(synthetic)} of {len(names)} held-out synthetic photos have exactly one face", flush=True)

    real_paths = sorted(args.real.glob("*/*.jpg")) if args.real else []
    real = crops_for(real_paths, ml)
    real_people = np.array([p.parent.name for p in real])
    print(f"{len(real)} of {len(real_paths)} real photos have exactly one face", flush=True)

    slices = sorted({r["source"] for r in rows if r.get("source")})
    table = [
        "| Model | Licence | Dim | Embed ms mean | Embed ms p95 | VRAM MiB | TAR@1e-3 | TAR@1e-4 | "
        + " | ".join(f"{s} @1e-3" for s in slices)
        + " | Real TAR@1e-2 | Real TAR@1e-3 | Real Rank-1 |",
        "|" + "---|" * (11 + len(slices)),
    ]
    idle = vram()
    for name, (factory, licence) in CANDIDATES.items():
        embedder = factory()
        times, vectors = [], {}
        for path, crop in {**synthetic, **real}.items():
            start = time.perf_counter()
            vectors[path] = embed_crop(embedder, crop)
            times.append(1000 * (time.perf_counter() - start))
        times = times[3:]
        used = vram() - idle
        genuine, impostor = defaultdict(list), []
        for row in rows:
            a, b = ROOT / "data" / row["a"], ROOT / "data" / row["b"]
            if a not in synthetic or b not in synthetic:
                continue
            score = float(vectors[a] @ vectors[b])
            if int(row["same"]):
                genuine["all"].append(score)
                genuine[row["source"]].append(score)
            else:
                impostor.append(score)
        impostor = np.array(impostor)
        cells = [
            name,
            licence,
            str(next(iter(vectors.values())).size),
            f"{np.mean(times):.1f}",
            f"{np.percentile(times, 95):.1f}",
            str(used),
            tar(np.array(genuine["all"]), impostor, 1e-3),
            tar(np.array(genuine["all"]), impostor, 1e-4),
        ]
        cells += [tar(np.array(genuine[s]), impostor, 1e-3) for s in slices]
        if real:
            matrix = np.stack([vectors[p] for p in real])
            scores = matrix @ matrix.T
            upper = np.triu_indices(len(matrix), k=1)
            same = real_people[upper[0]] == real_people[upper[1]]
            cells += [tar(scores[upper][same], scores[upper][~same], f) for f in (1e-2, 1e-3)]
            np.fill_diagonal(scores, -2)
            cells.append(f"{100 * float((real_people[scores.argmax(1)] == real_people).mean()):.1f}%")
        else:
            cells += ["n/a", "n/a", "n/a"]
        table.append("| " + " | ".join(cells) + " |")
        print(table[-1], flush=True)
        del embedder

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "# Model comparison\n\n"
        f"Held-out synthetic set: {len(synthetic)} photos (GAN faces, artificial degradation). "
        f"Real set: {len(real)} photos, totals only. "
        "Same detector and alignment for every model. VRAM is the rise above idle, on a shared GPU.\n\n"
        + "\n".join(table)
        + "\n\nLicences were read from upstream on 2026-11-05:\n\n"
        "- InsightFace README: code MIT, pretrained models non-commercial research only.\n"
        "- OpenCV Zoo `models/face_recognition_sface/LICENSE`: Apache-2.0. "
        "This covers the published files. The provenance of its training faces wasn't checked.\n"
        "- AdaFace: code MIT, weights trained on WebFace and MS1M with no stated weight licence, and "
        "distributed as PyTorch checkpoints on Google Drive. Skipped as unclear, as the plan says.\n"
    )


if __name__ == "__main__":
    main()

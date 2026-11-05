"""Build the synthetic fixture set in data/fixtures/synthetic/. Dev machines only.

Every base portrait comes from a GAN, so no real person is in it. The other "photos" of an
identity are degraded copies of that one portrait, which makes genuine pairs far easier than
real ones. Fine for exercising the pipeline. Useless for measuring real accuracy.

The generator makes all ages. Look through the portraits and delete any folder that reads as
under 18 before seeding: a child has no place in a demo of a criminal records gallery.

    uv run python scripts/make_fixtures.py --people 130
"""

import argparse
import csv
import hashlib
import sys
import time
import urllib.request
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from _paths import ROOT

SOURCE = "https://thispersondoesnotexist.com/random-person.jpeg"
OUT = ROOT / "data" / "fixtures" / "synthetic"
SIZE = 512


def fetch() -> bytes:
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read()


def rotate(image: np.ndarray, degrees: float, scale: float = 1.0) -> np.ndarray:
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, scale)
    return cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REFLECT)


def compress(image: np.ndarray, quality: int) -> np.ndarray:
    return cv2.imdecode(
        cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])[1], cv2.IMREAD_COLOR
    )


def phone(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = rotate(image, rng.uniform(-10, 10), rng.uniform(0.85, 1.1))
    out = cv2.convertScaleAbs(out, alpha=rng.uniform(0.75, 1.2), beta=rng.uniform(-25, 25))
    return compress(out, int(rng.integers(55, 80)))


def side_lit(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    ramp = np.linspace(rng.uniform(0.35, 0.6), rng.uniform(1.0, 1.25), image.shape[1])
    if rng.random() < 0.5:
        ramp = ramp[::-1]
    out = np.clip(image * ramp[None, :, None], 0, 255).astype(np.uint8)
    return compress(rotate(out, rng.uniform(-6, 6)), 70)


def cctv(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    small = int(rng.integers(72, 128))
    out = cv2.resize(image, (small, small), interpolation=cv2.INTER_AREA)
    out = cv2.resize(out, image.shape[:2][::-1], interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if rng.random() < 0.5 else out
    noise = rng.normal(0, rng.uniform(6, 16), out.shape)
    return compress(np.clip(out + noise, 0, 255).astype(np.uint8), int(rng.integers(25, 50)))


def heavy_blur(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return compress(cv2.GaussianBlur(image, (0, 0), rng.uniform(7, 16)), 60)


def occluded(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A scarf or mask over the lower face."""
    h, w = image.shape[:2]
    out = image.copy()
    top = int(h * rng.uniform(0.5, 0.62))
    out[top:, :] = rng.integers(20, 90)
    return compress(rotate(out, rng.uniform(-8, 8)), 60)


def tiny(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A face a few dozen pixels wide, enlarged from a distant frame."""
    small = int(rng.integers(40, 72))
    out = cv2.resize(image, (small, small), interpolation=cv2.INTER_AREA)
    out = cv2.resize(out, image.shape[:2][::-1], interpolation=cv2.INTER_CUBIC)
    return compress(out, 40)


# The last three are there so genuine scores overlap impostor scores, as real ones do.
# Without them the fitted curve collapses into a step.
VARIANTS = {
    "mugshot": None,
    "phone": phone,
    "sidelit": side_lit,
    "cctv": cctv,
    "blur": heavy_blur,
    "occluded": occluded,
    "tiny": tiny,
}
VARIANT_COPIES = {
    "mugshot": 1,
    "phone": 2,
    "sidelit": 1,
    "cctv": 2,
    "blur": 2,
    "occluded": 2,
    "tiny": 2,
}
SEVERITY = ["tiny", "occluded", "blur", "cctv", "sidelit", "phone", "mugshot"]


def build(base: np.ndarray, folder: Path, rng: np.random.Generator) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, transform in VARIANTS.items():
        for copy in range(VARIANT_COPIES[name]):
            path = folder / f"{name}_{copy}.jpg"
            if path.exists():
                continue
            image = base if transform is None else transform(base, rng)
            cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])


def write_pairs(people: list[Path], path: Path) -> None:
    """Every pair of images: same identity is genuine, different identity is impostor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    photos = [(p.name, f) for p in people for f in sorted(p.glob("*.jpg"))]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["a", "b", "same", "source", "gender", "age_band"])
        for (pa, fa), (pb, fb) in combinations(photos, 2):
            same = int(pa == pb)
            # Slice by whichever side is worse, since that's the photo an officer would hold.
            kinds = (fa.stem.split("_")[0], fb.stem.split("_")[0])
            source = next(s for s in SEVERITY if s in kinds)
            writer.writerow(
                [
                    fa.relative_to(ROOT / "data"),
                    fb.relative_to(ROOT / "data"),
                    same,
                    source,
                    "",
                    "",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--people", type=int, default=130)
    parser.add_argument("--heldout", type=int, default=40, help="identities kept for eval.py")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    seen = set()
    OUT.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in OUT.glob("p*") if p.is_dir())
    for folder in existing:
        build(cv2.imread(str(folder / "mugshot_0.jpg")), folder, rng)
    count = len(existing)
    while count < args.people:
        try:
            data = fetch()
        except OSError as e:
            print(f"fetch failed: {e}", file=sys.stderr)
            time.sleep(5)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            time.sleep(1.5)
            continue
        seen.add(digest)
        base = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if base is None:
            continue
        portrait = cv2.resize(base, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        build(portrait, OUT / f"p{count:03d}", rng)
        count += 1
        if count % 10 == 0:
            print(f"{count}/{args.people}", flush=True)
        time.sleep(1.0)

    people = sorted(p for p in OUT.glob("p*") if p.is_dir())
    write_pairs(people[: -args.heldout], ROOT / "data" / "eval" / "pairs.csv")
    write_pairs(people[-args.heldout :], ROOT / "data" / "eval" / "heldout.csv")
    print(f"{len(people)} identities, {args.heldout} held out")


if __name__ == "__main__":
    main()

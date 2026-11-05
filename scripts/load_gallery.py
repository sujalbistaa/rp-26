"""Scale test on real pgvector, in its own database. Synthetic vectors, no images, no people.

    uv run python scripts/load_gallery.py --sizes 20000 100000

Measures recall of the true person against an exact scan at several ef_search values, for
clean and weak probes, then whole-search latency with 1, 5 and 20 officers at once. A whole
search here is detect + embed on a fixture photo, then the same SQL the API runs.
"""

import argparse
import statistics
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from _paths import ROOT

from app.config import get_settings  # noqa: E402
from app.ml.align import align  # noqa: E402
from app.ml.embed import embed_crop  # noqa: E402
from app.pipeline import get_ml  # noqa: E402

ADMIN = "postgresql://rkl:rkl@localhost:5544/rkl"
SCALE = "postgresql://rkl:rkl@localhost:5544/rkl_scale"
DIM, IMAGES_PER_PERSON, TOP_K = 512, 4, 200
# Cosine of a photo to its person's centre. A pair of them then scores about the square.
CLEAN, WEAK, VERY_WEAK = 0.8, 0.55, 0.45
EF_VALUES = (100, 200, 400, 800)
OFFICERS = (1, 5, 20)
OUT = ROOT / "docs" / "results" / "scale.md"


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def around(centres: np.ndarray, cosine: float, rng: np.random.Generator) -> np.ndarray:
    noise = rng.standard_normal(centres.shape).astype(np.float32)
    noise -= (noise * centres).sum(-1, keepdims=True) * centres
    return unit(cosine * centres + np.sqrt(1 - cosine**2) * unit(noise))


def build(size: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    people = size // IMAGES_PER_PERSON
    centres = unit(rng.standard_normal((people, DIM)).astype(np.float32))
    owner = np.repeat(np.arange(people), IMAGES_PER_PERSON)
    return around(centres[owner], CLEAN, rng).astype(np.float32), owner, centres


def load(vectors: np.ndarray, owner: np.ndarray) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS rkl_scale")
        admin.execute("CREATE DATABASE rkl_scale")
    with psycopg.connect(SCALE, autocommit=True) as db:
        db.execute("CREATE EXTENSION vector")
        register_vector(db)
        db.execute(f"CREATE TABLE images (id int PRIMARY KEY, person int, v vector({DIM}))")
        with db.cursor().copy("COPY images (id, person, v) FROM STDIN WITH (FORMAT BINARY)") as copy:
            copy.set_types(["int4", "int4", "vector"])
            for i, (person, vector) in enumerate(zip(owner, vectors, strict=True)):
                copy.write_row((i, int(person), vector))
        start = time.perf_counter()
        db.execute("SET maintenance_work_mem = '1GB'")
        # Parallel builds need more /dev/shm than a default container has.
        db.execute("SET max_parallel_maintenance_workers = 0")
        db.execute(
            "CREATE INDEX ON images USING hnsw (v vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 200)"
        )
        print(f"index built in {time.perf_counter() - start:.0f} s", flush=True)
        db.execute("VACUUM ANALYZE images")


def query(db: psycopg.Connection, vector: np.ndarray, ef: int) -> list[tuple[int, int]]:
    with db.transaction():
        db.execute(f"SET LOCAL hnsw.ef_search = {int(ef)}")
        db.execute("SET LOCAL hnsw.iterative_scan = strict_order")
        rows = db.execute(
            "SELECT id, person FROM images ORDER BY v <=> %s LIMIT %s", (vector, TOP_K)
        ).fetchall()
    return rows


def recall(db, vectors, owner, centres, rng, probes: int) -> list[str]:
    picked = rng.choice(centres.shape[0], probes, replace=False)
    lines = ["| Probe | ef_search | True person found, HNSW | Same, exact scan | Top-200 overlap |", "|---|---|---|---|---|"]
    for name, cosine in (("clean", CLEAN), ("weak", WEAK), ("very weak", VERY_WEAK)):
        queries = around(centres[picked], cosine, rng).astype(np.float32)
        scores = queries @ vectors.T
        exact = np.argpartition(-scores, TOP_K, axis=1)[:, :TOP_K]
        exact_found = np.mean([picked[i] in owner[exact[i]] for i in range(probes)])
        for ef in EF_VALUES:
            found, overlap = 0, 0.0
            for i, vector in enumerate(queries):
                rows = query(db, vector, ef)
                found += any(person == picked[i] for _, person in rows)
                overlap += len({r[0] for r in rows} & set(exact[i].tolist())) / TOP_K
            lines.append(
                f"| {name} | {ef} | {100 * found / probes:.1f}% | {100 * exact_found:.1f}% | "
                f"{100 * overlap / probes:.1f}% |"
            )
            print(lines[-1], flush=True)
    return lines


def latency(images: list[np.ndarray], ml, ef: int, per_officer: int) -> list[str]:
    lines = ["| Officers | Searches | p50 ms | p95 ms | Max ms |", "|---|---|---|---|---|"]
    for officers in OFFICERS:
        times: list[float] = []

        def officer(seed: int, times=times) -> None:
            with psycopg.connect(SCALE, autocommit=True) as db:
                register_vector(db)
                rng = np.random.default_rng(seed)
                for _ in range(per_officer):
                    image = images[int(rng.integers(len(images)))]
                    start = time.perf_counter()
                    faces = ml.detector.detect(image)
                    vector = embed_crop(ml.embedder, align(image, faces[0].landmarks))
                    query(db, vector, ef)
                    times.append(1000 * (time.perf_counter() - start))

        threads = [threading.Thread(target=officer, args=(n,)) for n in range(officers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        p95 = float(np.percentile(times, 95))
        lines.append(
            f"| {officers} | {len(times)} | {statistics.median(times):.0f} | {p95:.0f} | {max(times):.0f} |"
        )
        print(lines[-1], flush=True)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[20_000, 100_000])
    parser.add_argument("--probes", type=int, default=300)
    parser.add_argument("--ef", type=int, default=400, help="ef_search for the latency run")
    parser.add_argument("--per-officer", type=int, default=30)
    args = parser.parse_args()

    rng = np.random.default_rng(11)
    ml = get_ml()
    paths: list[Path] = sorted((ROOT / "data" / "fixtures" / "synthetic").rglob("mugshot_0.jpg"))[:30]
    images = [cv2.imread(str(p)) for p in paths]
    images = [i for i in images if ml.detector.detect(i)]
    settings = get_settings()
    out = [
        "# Scale and recall",
        "",
        "Synthetic clustered vectors in a separate database, on real pgvector HNSW "
        "(m=16, ef_construction=200, iterative scan). No images, no people. Latency uses real "
        f"detection and embedding on synthetic fixture photos, embedder {settings.embedder_version}.",
    ]
    for size in args.sizes:
        print(f"== {size} images", flush=True)
        vectors, owner, centres = build(size, rng)
        load(vectors, owner)
        out += ["", f"## {size:,} images", ""]
        with psycopg.connect(SCALE, autocommit=True) as db:
            register_vector(db)
            out += recall(db, vectors, owner, centres, rng, args.probes)
        out += ["", f"Latency at ef_search {args.ef}:", ""]
        out += latency(images, ml, args.ef, args.per_officer)
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS rkl_scale")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

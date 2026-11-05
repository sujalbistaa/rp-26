"""Prove the GPU works: same models, same images, on CPU and on CUDA.

    uv run python scripts/gpu_check.py

Exits 1 if CUDA isn't active on both models, or if CPU and CUDA embeddings disagree.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ["ORT_DISABLE_TELEMETRY"] = "1"

import cv2
import numpy as np
import onnxruntime as ort

from _paths import ROOT

from app.config import get_settings  # noqa: E402
from app.ml.align import align  # noqa: E402
from app.ml.detect import ScrfdDetector  # noqa: E402
from app.ml.embed import ArcFaceEmbedder, embed_crop  # noqa: E402
from app.ml.runtime import CPU, CUDA, preload_cuda  # noqa: E402

N_IMAGES = 50
MIN_COSINE = 0.999
OUT = ROOT / "docs" / "results" / "gpu.md"


def vram_mib() -> int:
    out = subprocess.run(  # noqa: S603
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.split()[0])


class VramPeak:
    def __enter__(self):
        self.peak, self._stop = vram_mib(), threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _watch(self):
        while not self._stop.wait(0.1):
            self.peak = max(self.peak, vram_mib())

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


def pin(model, path: Path, provider: str):
    model.session = ort.InferenceSession(str(path), providers=[provider])
    return model.session.get_providers()[0]


def run(images, detector, embedder):
    det_ms, emb_ms, vectors = [], [], []
    for image in images:
        start = time.perf_counter()
        faces = detector.detect(image)
        det_ms.append(1000 * (time.perf_counter() - start))
        if not faces:
            vectors.append(None)
            continue
        crop = align(image, faces[0].landmarks)
        start = time.perf_counter()
        vectors.append(embed_crop(embedder, crop))
        emb_ms.append(1000 * (time.perf_counter() - start))
    return det_ms, emb_ms, vectors


def stats(values) -> str:
    return f"{np.mean(values):.1f} ms mean, {np.percentile(values, 95):.1f} ms p95"


def main() -> None:
    settings = get_settings()
    preload_cuda()
    paths = sorted((ROOT / "data" / "fixtures" / "synthetic").rglob("*.jpg"))[:N_IMAGES]
    images = [cv2.imread(str(p)) for p in paths]
    det_path = settings.model_dir / settings.detector_file
    emb_path = settings.model_dir / settings.embedder_file
    lines = [
        f"onnxruntime {ort.__version__}",
        f"available providers: {ort.get_available_providers()}",
        f"images: {len(images)} synthetic fixtures",
    ]
    detector = ScrfdDetector(det_path, settings.detect_threshold)
    embedder = ArcFaceEmbedder(emb_path, settings.embedder_version)
    results, failed = {}, []
    before = vram_mib()
    for provider in (CPU, CUDA):
        active = pin(detector, det_path, provider), pin(embedder, emb_path, provider)
        lines.append(f"{provider}: active detector={active[0]} embedder={active[1]}")
        if any(a != provider for a in active):
            failed.append(f"{provider} requested, got {active}")
        with VramPeak() as vram:
            run(images[:3], detector, embedder)  # warm-up, excluded from timing
            det_ms, emb_ms, vectors = run(images, detector, embedder)
        results[provider] = vectors
        lines += [f"  detect: {stats(det_ms)}", f"  embed:  {stats(emb_ms)}"]
    lines.append(f"peak VRAM: {vram.peak} MiB ({vram.peak - before} MiB above idle)")
    pairs = [(a, b) for a, b in zip(results[CPU], results[CUDA], strict=True) if a is not None and b is not None]
    lowest = min(float(a @ b) for a, b in pairs)
    lines.append(f"min CPU/CUDA cosine over {len(pairs)} faces: {lowest:.6f} (needs {MIN_COSINE})")
    if lowest < MIN_COSINE:
        failed.append("embeddings differ between CPU and CUDA")
    lines.append("RESULT: " + ("FAIL: " + "; ".join(failed) if failed else "PASS"))
    report = "\n".join(lines)
    print(report)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f"# GPU check\n\nSynthetic fixtures. Output of `scripts/gpu_check.py`.\n\n```\n{report}\n```\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

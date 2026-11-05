import logging
import threading

import numpy as np
import onnxruntime as ort

from app.config import get_settings

ERROR_ONLY = 3
CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"

log = logging.getLogger(__name__)
_preloaded = False
# FastAPI runs requests on a thread pool. Twenty threads calling the GPU at once took p95 from
# under a second to 42 seconds in scripts/load_gallery.py, so inference goes one call at a time.
_inference = threading.Lock()


def preload_cuda() -> None:
    """Load the CUDA and cuDNN libraries from the pip wheels. Needed before the first session."""
    global _preloaded
    if not _preloaded and hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    _preloaded = True


def infer(session: ort.InferenceSession, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
    with _inference:
        return session.run(None, feeds)


def load_session(path: str) -> ort.InferenceSession:
    preload_cuda()
    available = set(ort.get_available_providers())
    options = ort.SessionOptions()
    # The ArcFace export declares a batch of 1, so a flip pair logs a shape warning per call.
    options.log_severity_level = ERROR_ONLY
    session = ort.InferenceSession(
        path, sess_options=options, providers=[p for p in (CUDA, CPU) if p in available]
    )
    # ORT drops to CPU without raising if CUDA can't start, so check what really loaded.
    active = session.get_providers()
    log.info("%s runs on %s", path, active[0])
    if get_settings().require_gpu and active[0] != CUDA:
        raise RuntimeError(f"RKL_REQUIRE_GPU is set but {path} runs on {active[0]}")
    return session

import logging
import os
import threading

# ORT builds in Microsoft telemetry (1DS). This app makes zero external calls. The variable has
# to be set before ORT starts. disable_telemetry_events() alone left the connection in place.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from app.config import get_settings  # noqa: E402

# ORT builds in Microsoft telemetry. This app makes zero external calls, so it's always off.
if hasattr(ort, "disable_telemetry_events"):
    ort.disable_telemetry_events()

ERROR_ONLY = 3
CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"

log = logging.getLogger(__name__)
_preloaded = False
# FastAPI runs requests on a thread pool. Twenty threads calling the GPU at once took p95 from
# under a second to 42 seconds in scripts/load_gallery.py, so inference goes one call at a time.
# The lock is per process, which is why the API runs a single uvicorn worker.
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

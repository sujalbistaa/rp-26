import onnxruntime as ort

ERROR_ONLY = 3


def load_session(path: str) -> ort.InferenceSession:
    available = set(ort.get_available_providers())
    wanted = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    options = ort.SessionOptions()
    # The ArcFace export declares a batch of 1, so a flip pair logs a shape warning per call.
    options.log_severity_level = ERROR_ONLY
    return ort.InferenceSession(
        path, sess_options=options, providers=[p for p in wanted if p in available]
    )

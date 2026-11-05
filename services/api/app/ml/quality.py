from dataclasses import asdict, dataclass

import cv2
import numpy as np

from app.config import Settings
from app.ml.detect import Face


@dataclass(frozen=True)
class Quality:
    face_px: int
    blur: float
    yaw: float
    too_small: bool
    too_blurry: bool
    too_turned: bool

    @property
    def low(self) -> bool:
        return self.too_small or self.too_blurry or self.too_turned

    def as_dict(self) -> dict:
        return {**asdict(self), "low": self.low}


def estimate_yaw(landmarks: np.ndarray) -> float:
    """Rough yaw in degrees from where the nose sits between the eyes.

    Measured along the eye axis so head roll doesn't read as yaw. It's a gate, not a pose
    estimate: about 0 when frontal, and it reaches 90 once the nose passes an eye.
    """
    left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]
    axis = right_eye - left_eye
    length = float(np.linalg.norm(axis))
    if length < 1e-6:
        return 90.0
    position = float(np.dot(nose - left_eye, axis / length)) / length  # 0.5 when frontal
    return float(np.clip((position - 0.5) * 2, -1, 1) * 90)


def assess(face: Face, crop: np.ndarray, settings: Settings) -> Quality:
    """Blur is measured on the aligned crop, so it doesn't depend on the photo's resolution."""
    x1, y1, x2, y2 = face.box
    face_px = int(min(x2 - x1, y2 - y1))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    yaw = estimate_yaw(face.landmarks)
    return Quality(
        face_px=face_px,
        blur=round(blur, 1),
        yaw=round(yaw, 1),
        too_small=face_px < settings.quality_min_face_px,
        too_blurry=blur < settings.quality_min_blur,
        too_turned=abs(yaw) > settings.quality_max_yaw,
    )

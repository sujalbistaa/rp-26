import cv2
import numpy as np

CROP_SIZE = 112
# ArcFace reference positions for the five landmarks in a 112x112 crop.
TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def align(bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    matrix, _ = cv2.estimateAffinePartial2D(
        landmarks.astype(np.float32), TEMPLATE, method=cv2.LMEDS
    )
    if matrix is None:
        raise ValueError("landmarks are degenerate")
    return cv2.warpAffine(bgr, matrix, (CROP_SIZE, CROP_SIZE), flags=cv2.INTER_LINEAR)

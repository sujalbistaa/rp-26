"""SCRFD face detection, run directly on onnxruntime."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from app.ml.runtime import load_session

INPUT_SIZE = 640
STRIDES = (8, 16, 32)
ANCHORS_PER_CELL = 2
NMS_IOU = 0.4
# SCRFD's largest anchors stop well short of the input size, so a face filling the frame (a
# mugshot, a phone close-up) scores near zero at full scale. The half-scale pass catches it.
PASS_SCALES = (1.0, 0.5)


@dataclass(frozen=True)
class Face:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in source pixels
    score: float
    landmarks: np.ndarray  # (5, 2): left eye, right eye, nose, left mouth, right mouth


class Detector(Protocol):
    def detect(self, bgr: np.ndarray) -> list[Face]: ...


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        overlap = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[overlap <= iou]
    return keep


class ScrfdDetector:
    def __init__(self, model_path: Path, threshold: float) -> None:
        self.session = load_session(str(model_path))
        self.input_name = self.session.get_inputs()[0].name
        self.threshold = threshold
        self._centers: dict[int, np.ndarray] = {}

    def _anchor_centers(self, stride: int) -> np.ndarray:
        if stride not in self._centers:
            size = INPUT_SIZE // stride
            grid = np.stack(np.mgrid[:size, :size][::-1], axis=-1).astype(np.float32)
            centers = (grid * stride).reshape(-1, 2)
            self._centers[stride] = np.repeat(centers, ANCHORS_PER_CELL, axis=0)
        return self._centers[stride]

    def _forward(
        self, bgr: np.ndarray, scale: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """One pass. Returns boxes, scores and landmarks in source pixels, or None."""
        h, w = bgr.shape[:2]
        resized = cv2.resize(bgr, (int(round(w * scale)), int(round(h * scale))))
        canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(
            canvas, 1 / 128, (INPUT_SIZE, INPUT_SIZE), (127.5, 127.5, 127.5), swapRB=True
        )
        outputs = self.session.run(None, {self.input_name: blob})

        boxes, scores, points = [], [], []
        n = len(STRIDES)
        for i, stride in enumerate(STRIDES):
            cls = outputs[i].reshape(-1)
            hits = np.where(cls >= self.threshold)[0]
            if hits.size == 0:
                continue
            centers = self._anchor_centers(stride)[hits]
            dist = outputs[i + n].reshape(-1, 4)[hits] * stride
            kps = outputs[i + 2 * n].reshape(-1, 10)[hits] * stride
            boxes.append(
                np.stack(
                    [
                        centers[:, 0] - dist[:, 0],
                        centers[:, 1] - dist[:, 1],
                        centers[:, 0] + dist[:, 2],
                        centers[:, 1] + dist[:, 3],
                    ],
                    axis=1,
                )
            )
            scores.append(cls[hits])
            points.append(kps.reshape(-1, 5, 2) + centers[:, None, :])
        if not boxes:
            return None
        return (
            np.concatenate(boxes) / scale,
            np.concatenate(scores),
            np.concatenate(points) / scale,
        )

    def detect(self, bgr: np.ndarray) -> list[Face]:
        h, w = bgr.shape[:2]
        fit = INPUT_SIZE / max(h, w)
        # Both passes always run. Stopping after the first could hide a large foreground face
        # behind a small background one, and the officer has to see every face to pick.
        passes = [p for p in (self._forward(bgr, fit * s) for s in PASS_SCALES) if p is not None]
        if not passes:
            return []
        all_boxes = np.concatenate([p[0] for p in passes])
        all_scores = np.concatenate([p[1] for p in passes])
        all_points = np.concatenate([p[2] for p in passes])
        keep = _nms(all_boxes, all_scores, NMS_IOU)

        faces = [
            Face(
                box=(
                    float(max(0, all_boxes[i, 0])),
                    float(max(0, all_boxes[i, 1])),
                    float(min(w, all_boxes[i, 2])),
                    float(min(h, all_boxes[i, 3])),
                ),
                score=float(all_scores[i]),
                landmarks=all_points[i],
            )
            for i in keep
        ]
        # Left to right, so FACE 1 and FACE 2 read the way the officer scans the photo.
        return sorted(faces, key=lambda f: f.box[0])

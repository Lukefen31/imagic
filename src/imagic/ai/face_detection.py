"""AI-powered face detection and recognition.

Detects faces in photos and can group/index them for organization.
Uses OpenCV's DNN face detector with optional dlib landmarks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FaceBox:
    """A detected face bounding box."""
    x: int
    y: int
    width: int
    height: int
    confidence: float
    landmarks: Optional[List[Tuple[int, int]]] = None
    label: str = ""  # User-assigned name
    embedding: Optional[np.ndarray] = None  # Face encoding for recognition


@dataclass
class FaceDetectionResult:
    """Result of face detection on an image."""
    faces: List[FaceBox] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0


def detect_faces(img: np.ndarray, min_confidence: float = 0.5) -> FaceDetectionResult:
    """Detect faces in an image.

    Tries OpenCV DNN detector first, falls back to Haar cascades.

    Args:
        img: uint8 RGB (H, W, 3).
        min_confidence: Minimum detection confidence (0–1).

    Returns:
        FaceDetectionResult with list of detected FaceBox instances.
    """
    h, w = img.shape[:2]
    result = FaceDetectionResult(image_width=w, image_height=h)

    # Try OpenCV DNN face detector
    faces = _detect_opencv_dnn(img, min_confidence)
    if faces is not None:
        result.faces = faces
        return result

    # Fallback: Haar cascade
    faces = _detect_haar(img, min_confidence)
    if faces is not None:
        result.faces = faces

    return result


def _detect_opencv_dnn(img: np.ndarray, min_conf: float) -> Optional[List[FaceBox]]:
    """Use OpenCV's DNN module for face detection."""
    try:
        import cv2
        h, w = img.shape[:2]

        # Use OpenCV's built-in Yunet face detector if available (4.5.4+)
        try:
            detector = cv2.FaceDetectorYN.create(
                "",  # model path — empty uses built-in
                "",
                (w, h),
                min_conf,
            )
        except (cv2.error, AttributeError):
            # YuNet not available, try Haar
            return None

        # Detect
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        _, detections = detector.detect(bgr)

        if detections is None:
            return []

        faces = []
        for det in detections:
            x, y, bw, bh = int(det[0]), int(det[1]), int(det[2]), int(det[3])
            conf = float(det[-1])
            if conf >= min_conf:
                faces.append(FaceBox(
                    x=max(0, x), y=max(0, y),
                    width=min(bw, w - x), height=min(bh, h - y),
                    confidence=conf,
                ))
        return faces

    except ImportError:
        return None
    except Exception:
        logger.exception("DNN face detection failed")
        return None


def _detect_haar(img: np.ndarray, min_conf: float) -> Optional[List[FaceBox]]:
    """Fallback face detection using Haar cascades."""
    try:
        import cv2
        h, w = img.shape[:2]

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        if not Path(cascade_path).exists():
            return None

        cascade = cv2.CascadeClassifier(cascade_path)
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Scale for performance
        scale = min(1.0, 800 / max(h, w))
        if scale < 1.0:
            small = cv2.resize(gray, None, fx=scale, fy=scale)
        else:
            small = gray

        rects = cascade.detectMultiScale(small, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

        faces = []
        for (fx, fy, fw, fh) in rects:
            if scale < 1.0:
                fx, fy, fw, fh = (
                    int(fx / scale), int(fy / scale),
                    int(fw / scale), int(fh / scale),
                )
            faces.append(FaceBox(
                x=fx, y=fy, width=fw, height=fh,
                confidence=0.8,  # Haar doesn't give confidence
            ))

        return faces

    except ImportError:
        return None
    except Exception:
        logger.exception("Haar face detection failed")
        return None


def compute_face_embedding(img: np.ndarray, face: FaceBox) -> Optional[np.ndarray]:
    """Compute a 128-D face embedding for recognition/grouping.

    Uses dlib's face recognition model if available.
    """
    try:
        import dlib

        # Crop face region with margin
        h, w = img.shape[:2]
        margin = int(face.width * 0.2)
        x1 = max(0, face.x - margin)
        y1 = max(0, face.y - margin)
        x2 = min(w, face.x + face.width + margin)
        y2 = min(h, face.y + face.height + margin)
        crop = img[y1:y2, x1:x2]

        # Get dlib face encoding
        sp = dlib.shape_predictor(
            str(Path.home() / ".imagic" / "models" / "shape_predictor_68_face_landmarks.dat")
        )
        facerec = dlib.face_recognition_model_v1(
            str(Path.home() / ".imagic" / "models" / "dlib_face_recognition_resnet_model_v1.dat")
        )

        rect = dlib.rectangle(0, 0, crop.shape[1], crop.shape[0])
        shape = sp(crop, rect)
        embedding = np.array(facerec.compute_face_descriptor(crop, shape))
        face.embedding = embedding
        return embedding

    except (ImportError, Exception):
        logger.debug("Face embedding computation unavailable (dlib not installed)")
        return None


def match_faces(embedding: np.ndarray, known_embeddings: dict, threshold: float = 0.6) -> Optional[str]:
    """Match a face embedding against known faces.

    Args:
        embedding: 128-D numpy array.
        known_embeddings: Dict mapping name → embedding array.
        threshold: Maximum distance for a match.

    Returns:
        Name of the closest match, or None.
    """
    best_name = None
    best_dist = threshold

    for name, known in known_embeddings.items():
        dist = np.linalg.norm(embedding - known)
        if dist < best_dist:
            best_dist = dist
            best_name = name

    return best_name

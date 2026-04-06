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
            # Locate the bundled YuNet ONNX model
            import os
            model_path = os.path.join(
                os.path.dirname(cv2.__file__), "data",
                "face_detection_yunet_2023mar.onnx",
            )
            if not os.path.isfile(model_path):
                # No bundled model — skip YuNet
                return None

            detector = cv2.FaceDetectorYN.create(
                model_path,
                "",
                (w, h),
                min_conf,
            )
            # Detect
            bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            _, detections = detector.detect(bgr)
        except (cv2.error, AttributeError, Exception):
            # YuNet not available or failed, fall back to Haar
            return None

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
        alt_path = cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
        profile_path = cv2.data.haarcascades + "haarcascade_profileface.xml"

        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Equalise histogram for better detection in shadows / uneven lighting
        gray = cv2.equalizeHist(gray)

        # Scale for performance
        scale = min(1.0, 1200 / max(h, w))
        if scale < 1.0:
            small = cv2.resize(gray, None, fx=scale, fy=scale)
        else:
            small = gray

        faces = []
        seen = set()  # deduplicate overlapping detections

        def _add_rects(rects, conf=0.8):
            for (fx, fy, fw, fh) in rects:
                if scale < 1.0:
                    fx, fy, fw, fh = (
                        int(fx / scale), int(fy / scale),
                        int(fw / scale), int(fh / scale),
                    )
                # Simple dedup: skip if center is near an existing face
                cx, cy = fx + fw // 2, fy + fh // 2
                dup = False
                for (ex, ey) in seen:
                    if abs(cx - ex) < fw // 2 and abs(cy - ey) < fh // 2:
                        dup = True
                        break
                if not dup:
                    seen.add((cx, cy))
                    faces.append(FaceBox(
                        x=fx, y=fy, width=fw, height=fh,
                        confidence=conf,
                    ))

        # Try default frontal cascade with relaxed parameters
        if Path(cascade_path).exists():
            cascade = cv2.CascadeClassifier(cascade_path)
            rects = cascade.detectMultiScale(
                small, scaleFactor=1.08, minNeighbors=3, minSize=(20, 20),
            )
            _add_rects(rects, 0.8)

        # Try alt2 cascade (better with rotated / partially occluded faces)
        if Path(alt_path).exists():
            cascade2 = cv2.CascadeClassifier(alt_path)
            rects2 = cascade2.detectMultiScale(
                small, scaleFactor=1.08, minNeighbors=3, minSize=(20, 20),
            )
            _add_rects(rects2, 0.7)

        # Try profile cascade
        if Path(profile_path).exists():
            profile = cv2.CascadeClassifier(profile_path)
            rects3 = profile.detectMultiScale(
                small, scaleFactor=1.08, minNeighbors=3, minSize=(20, 20),
            )
            _add_rects(rects3, 0.6)
            # Also try flipped image for the other profile
            rects4 = profile.detectMultiScale(
                cv2.flip(small, 1), scaleFactor=1.08, minNeighbors=3, minSize=(20, 20),
            )
            sw = small.shape[1]
            flipped = [(sw - fx - fw, fy, fw, fh) for (fx, fy, fw, fh) in rects4]
            _add_rects(flipped, 0.6)

        return faces if faces else []

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

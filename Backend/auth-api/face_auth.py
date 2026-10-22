"""Local 1:1 face comparison. Uploaded images are a development input, not liveness."""
import io
import json
import math
import os
import threading
import uuid
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException

MAX_IMAGE_BYTES = 4 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 12_000_000
MODEL_DIR = Path(os.getenv("FACE_MODEL_DIR", "/app/face_models"))
REFERENCE_DIR = Path(os.getenv("FACE_REFERENCE_DIR", "/app/face_references"))
_lock = threading.Lock()


def reference_path(user_id):
    # IDs never become arbitrary paths, even when used from the operator CLI.
    return REFERENCE_DIR / (str(uuid.UUID(user_id)) + ".json")


def image_from_bytes(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image requise, maximum 4 Mo")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG"):
                raise ValueError("Utiliser une image JPEG ou PNG")
            if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("Image trop grande (12 megapixels maximum)")
            rgb = np.array(ImageOps.exif_transpose(image).convert("RGB"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Image JPEG ou PNG invalide") from exc
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    scale = min(1.0, 1280 / max(bgr.shape[:2]))
    if scale < 1:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale)
    return bgr


def models_available():
    return all((MODEL_DIR / name).is_file() for name in (
        "face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"))


@lru_cache(maxsize=1)
def _models():
    if not models_available():
        raise RuntimeError("Modeles faciaux absents : reconstruire auth-api")
    detector = cv2.FaceDetectorYN.create(
        str(MODEL_DIR / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.9)
    recognizer = cv2.FaceRecognizerSF.create(
        str(MODEL_DIR / "face_recognition_sface_2021dec.onnx"), "")
    return detector, recognizer


def feature(data):
    image = image_from_bytes(data)
    with _lock:
        detector, recognizer = _models()
        detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = detector.detect(image)
        if faces is None or len(faces) != 1:
            raise ValueError("Une seule personne doit etre visible, visage de face et bien eclaire")
        aligned = recognizer.alignCrop(image, faces[0])
        return recognizer.feature(aligned).reshape(1, -1)


def compare(user_id, data, reference_values=None):
    if reference_values is None:
        path = reference_path(user_id)
        if not path.is_file():
            raise HTTPException(409, "Reference absente : contacter l'operateur")
        values = json.loads(path.read_text(encoding="utf-8"))
    else:
        values = reference_values
    reference = np.asarray(values, dtype=np.float32).reshape(1, -1)
    if reference.shape != (1, 128) or not np.isfinite(reference).all():
        raise RuntimeError("Reference faciale invalide")
    probe = feature(data)
    with _lock:
        _, recognizer = _models()
        similarity = recognizer.match(reference, probe, cv2.FaceRecognizerSF_FR_COSINE)
    threshold = float(os.getenv("FACE_MATCH_THRESHOLD", "0.363"))
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise RuntimeError("Seuil facial invalide")
    return bool(similarity >= threshold)

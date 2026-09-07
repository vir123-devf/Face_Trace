"""Visual embeddings for image-to-image cosine similarity (no names or text)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from tf_keras.applications.mobilenet_v2 import MobileNetV2, preprocess_input
from tf_keras.preprocessing.image import img_to_array

MODEL_NAME = "MobileNetV2"
INPUT_SIZE = 224

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = MobileNetV2(
            weights="imagenet",
            include_top=False,
            pooling="avg",
            input_shape=(INPUT_SIZE, INPUT_SIZE, 3),
        )
    return _MODEL


def _prepare(image: Image.Image) -> np.ndarray:
    rgb = image.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR)
    batch = np.expand_dims(img_to_array(rgb), axis=0)
    return preprocess_input(batch)


def embed_pil(image: Image.Image) -> list[float]:
    vector = _model().predict(_prepare(image), verbose=0)[0]
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector.astype(np.float64).tolist()
    return (vector / norm).astype(np.float64).tolist()


def embed_image_file(image_path: str | Path) -> dict:
    image = Image.open(image_path).convert("RGB")
    return {
        "embedding": embed_pil(image),
        "model": MODEL_NAME,
        "source_image": str(image_path),
    }

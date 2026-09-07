from pathlib import Path

import cv2
import numpy as np
from deepface import DeepFace
from PIL import Image

DETECTOR = "opencv"
MIN_OUTPUT_SIZE = 320
# Extra space around the detector box so hair, ears, and chin stay in frame.
PAD_LEFT_RIGHT = 0.40
PAD_TOP = 0.55
PAD_BOTTOM = 0.40


def _largest_face(faces: list[dict]) -> dict:
    def area(face: dict) -> int:
        box = face.get("facial_area") or {}
        return int(box.get("w") or 0) * int(box.get("h") or 0)

    return max(faces, key=area)


def _hide_background(image: Image.Image) -> Image.Image:
    """Soften only the far corners; keep the full face visible."""
    array = np.array(image)
    height, width = array.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(
        mask,
        (width // 2, int(height * 0.50)),
        (int(width * 0.48), int(height * 0.50)),
        0,
        0,
        360,
        255,
        -1,
    )
    mask = cv2.GaussianBlur(mask, (61, 61), 0)
    weight = (mask.astype(np.float32) / 255.0)[..., None]
    background = np.full_like(array, 230)
    blended = array.astype(np.float32) * weight + background.astype(np.float32) * (1.0 - weight)
    return Image.fromarray(blended.astype(np.uint8), mode="RGB")


def crop_face(
    image_path: str,
    output_path: str | None = None,
) -> dict:
    """Detect the largest human face and save a wide crop that covers the whole head."""
    source = Path(image_path)
    faces = DeepFace.extract_faces(
        img_path=str(source),
        detector_backend=DETECTOR,
        enforce_detection=True,
        align=False,
    )
    chosen = _largest_face(faces)
    box = chosen["facial_area"]

    image = Image.open(source).convert("RGB")
    width, height = image.size
    pad_x = int(box["w"] * PAD_LEFT_RIGHT)
    pad_top = int(box["h"] * PAD_TOP)
    pad_bottom = int(box["h"] * PAD_BOTTOM)
    left = max(0, int(box["x"]) - pad_x)
    top = max(0, int(box["y"]) - pad_top)
    right = min(width, int(box["x"]) + int(box["w"]) + pad_x)
    bottom = min(height, int(box["y"]) + int(box["h"]) + pad_bottom)
    cropped = _hide_background(image.crop((left, top, right, bottom)))

    if min(cropped.size) < MIN_OUTPUT_SIZE:
        scale = MIN_OUTPUT_SIZE / min(cropped.size)
        cropped = cropped.resize(
            (
                max(MIN_OUTPUT_SIZE, int(cropped.width * scale)),
                max(MIN_OUTPUT_SIZE, int(cropped.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )

    dest = Path(output_path) if output_path else source.with_name(f"{source.stem}_face.jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dest, format="JPEG", quality=95)

    return {
        "source_image": str(source),
        "cropped_path": str(dest),
        "face_area": {
            "x": int(box["x"]),
            "y": int(box["y"]),
            "w": int(box["w"]),
            "h": int(box["h"]),
        },
        "crop_box": {"left": left, "top": top, "right": right, "bottom": bottom},
        "face_count": len(faces),
    }


if __name__ == "__main__":
    import json
    import sys

    result = crop_face(sys.argv[1])
    print(json.dumps(result, indent=2))

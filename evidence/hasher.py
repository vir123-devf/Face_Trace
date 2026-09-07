import hashlib
import json
import time


def build_evidence_bundle(
    image_data: dict,
    match: dict,
    confidence: float,
    matches: list[dict] | None = None,
) -> dict:
    top_matches = matches or [match]
    return {
        "source_image": image_data["source_image"],
        "cropped_image": image_data.get("cropped_image"),
        "face_area": image_data.get("face_area"),
        "cosine_input_vs_crop": image_data.get("cosine_input_vs_crop"),
        "matched_url": match["url"],
        "match_type": match.get("match_type"),
        "platform": match.get("platform"),
        "matched_urls": [item.get("url") for item in top_matches if item.get("url")],
        "embedding_model": image_data.get("model") or match.get("embedding_model"),
        "cosine_similarity": match.get("cosine_similarity", confidence),
        "compared_image_url": match.get("compared_image_url"),
        "confidence": confidence,
        "timestamp": int(time.time()),
    }


def hash_bundle(bundle: dict) -> str:
    encoded = json.dumps(bundle, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

"""Cosine similarity between the cropped face and images found by web search."""

from __future__ import annotations

import io
from urllib.parse import unquote, urlparse

import numpy as np
import requests
from PIL import Image, UnidentifiedImageError

from face_id.visual_embed import MODEL_NAME, embed_pil

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _USER_AGENT, "Accept": "image/*,*/*;q=0.8"})

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def cosine_similarity(vec_a, vec_b) -> float:
    a = np.asarray(vec_a, dtype=np.float64).ravel()
    b = np.asarray(vec_b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


def _looks_like_image_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    host = parsed.netloc.lower()
    if path.endswith(_IMAGE_SUFFIXES) or "upload.wikimedia.org" in host:
        return True
    return any(ext[1:] in query for ext in _IMAGE_SUFFIXES)


def _commons_file_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "wikipedia.org" not in host and "wikimedia.org" not in host:
        return None
    path = unquote(parsed.path)
    marker = "/wiki/File:"
    idx = path.find(marker)
    if idx < 0:
        idx = path.lower().find("/wiki/file:")
        if idx < 0:
            return None
    filename = path[idx + len("/wiki/File:") :]
    if not filename:
        return None
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + filename.replace(" ", "_")


def image_url_for_match(match: dict) -> str | None:
    for key in ("url", "compared_image_url"):
        url = match.get(key) or ""
        if _looks_like_image_url(url):
            return url
        file_url = _commons_file_url(url)
        if file_url:
            return file_url
    return match.get("url") or None


def embedding_from_image_url(image_url: str) -> list[float] | None:
    response = _SESSION.get(image_url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "html" in content_type or "text/plain" in content_type:
        return None
    try:
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
    except UnidentifiedImageError:
        return None
    return embed_pil(image)


def score_matches(crop_embedding, matches: list[dict], input_embedding=None) -> list[dict]:
    """Cosine similarity: cropped face vs each image retrieved from web search."""
    scored: list[dict] = []
    for match in matches:
        item = dict(match)
        item["embedding_model"] = MODEL_NAME
        item["cosine_similarity"] = None
        try:
            image_url = image_url_for_match(item)
            if not image_url:
                scored.append(item)
                continue
            item["compared_image_url"] = image_url
            web_embedding = embedding_from_image_url(image_url)
            if web_embedding is not None:
                item["cosine_similarity"] = round(
                    cosine_similarity(crop_embedding, web_embedding), 6
                )
        except (requests.RequestException, OSError, ValueError):
            pass
        scored.append(item)

    scored.sort(
        key=lambda item: (
            item.get("cosine_similarity") is not None,
            item.get("cosine_similarity") or -1.0,
        ),
        reverse=True,
    )
    return scored

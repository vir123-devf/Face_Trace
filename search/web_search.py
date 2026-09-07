import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import vision
import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# API NEEDED HERE: Google Cloud Vision API
# 1. Create a project at https://console.cloud.google.com
# 2. Enable the "Cloud Vision API"
# 3. Create a service account, download its JSON key
# 4. Set this environment variable to the key file's path:
#    export GOOGLE_APPLICATION_CREDENTIALS="/path/to/key.json"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CREDENTIALS = _PROJECT_ROOT / "web-search-507806-20e429f6ef0b.json"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _USER_AGENT})

TOP_N = 10

_PLATFORM_HOSTS = (
    ("wikipedia.org", "wikipedia", 40),
    ("wikimedia.org", "wikimedia", 38),
    ("wikidata.org", "wikidata", 36),
    ("linkedin.com", "linkedin", 36),
    ("instagram.com", "instagram", 34),
    ("facebook.com", "facebook", 32),
    ("fb.com", "facebook", 32),
    ("x.com", "twitter", 32),
    ("twitter.com", "twitter", 32),
    ("youtube.com", "youtube", 22),
    ("youtu.be", "youtube", 22),
    ("tiktok.com", "tiktok", 30),
)

_SOCIAL_POST_PATHS = {
    "instagram": ("/p/", "/reel/", "/tv/"),
    "facebook": ("/posts/", "/photos/", "/videos/", "/reel/", "/permalink.php"),
    "twitter": ("/status/",),
    "youtube": ("/watch", "/shorts/"),
    "tiktok": ("/video/",),
    "linkedin": ("/posts/", "/feed/update/"),
}

_TYPE_SCORE = {
    "full_image_match": 50,
    "partial_image_match": 30,
    "visually_similar": 40,
    "full_page_match": 10,
    "social_post": 55,
}

_PREFERRED_PLATFORMS = (
    "wikipedia",
    "linkedin",
    "instagram",
    "facebook",
    "twitter",
    "youtube",
    "official",
    "wikimedia",
    "wikidata",
)


def _ensure_credentials() -> None:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    if _DEFAULT_CREDENTIALS.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_DEFAULT_CREDENTIALS)


def _platform_for(url: str) -> tuple[str, int]:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for suffix, name, boost in _PLATFORM_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return name, boost
    if host.endswith(".gov.in") or host.endswith(".gov"):
        return "official", 28
    return "web", 0


def _social_post_platform(url: str) -> str | None:
    platform, _ = _platform_for(url)
    if platform not in _SOCIAL_POST_PATHS:
        return None
    path = urlparse(url).path.lower()
    query = urlparse(url).query.lower()
    if any(marker in path for marker in _SOCIAL_POST_PATHS[platform]):
        return platform
    return "youtube" if platform == "youtube" and "v=" in query else None


def _live_social_post(url: str) -> bool:
    if not _social_post_platform(url):
        return False
    try:
        response = _SESSION.get(url, timeout=20, allow_redirects=True, stream=True)
        return response.status_code < 400
    except requests.RequestException:
        return False


def _url_bonus(url: str, platform: str, match_type: str) -> int:
    lower = (url or "").lower()
    if platform == "wikipedia":
        if "/wiki/file:" in lower or "upload.wikimedia.org" in lower:
            return -8
        if "/wiki/" in lower:
            return 14
    if platform == "linkedin" and "/in/" in lower:
        return 10
    if platform == "instagram":
        if "lookaside.instagram.com" in lower or "google_widget" in lower:
            return -40
        if "/p/" not in lower and "/reel/" not in lower:
            return 8
        return 0
    if platform == "facebook" and "/videos/" not in lower:
        return 6
    if platform == "youtube":
        if "watch?" in lower or "/shorts/" in lower:
            return -24
        if "/channel/" in lower or "/@" in lower or "/c/" in lower or "/user/" in lower:
            return 10
    if platform == "twitter" and "/status/" in lower:
        return -10
    if match_type == "visually_similar":
        return 6
    return 0


def _annotate(match: dict) -> dict:
    url = match.get("url") or ""
    platform, boost = _platform_for(url)
    match["platform"] = match.get("platform") or platform
    type_score = _TYPE_SCORE.get(match.get("match_type") or "", 10)
    platform_score = boost
    if match.get("platform") and match["platform"] != platform:
        platform_score = dict(
            wikipedia=40,
            wikimedia=38,
            wikidata=36,
            linkedin=36,
            instagram=34,
            facebook=32,
            twitter=32,
            youtube=22,
            official=28,
            web=0,
        ).get(match["platform"], boost)
    match["score"] = type_score + platform_score + _url_bonus(
        url, match["platform"], match.get("match_type") or ""
    )
    return match


def _add_image(matches: list[dict], url: str, match_type: str, page_url: str | None = None) -> None:
    if not url:
        return
    matches.append(
        {
            "url": url,
            "match_type": match_type,
            "page_url": page_url,
        }
    )


def _vision_visual_matches(image_path: str) -> list[dict]:
    """Reverse-image search only. Does not read names, text, or web-entity labels."""
    _ensure_credentials()
    client = vision.ImageAnnotatorClient()

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    # web_detection uses the pixels of the image, not OCR or name extraction.
    response = client.web_detection(image=image)

    if response.error.message:
        raise RuntimeError(response.error.message)

    web = response.web_detection
    matches: list[dict] = []

    for page in web.pages_with_matching_images:
        for img in page.full_matching_images:
            _add_image(matches, img.url, "full_image_match", page.url)
        for img in page.partial_matching_images:
            _add_image(matches, img.url, "partial_image_match", page.url)
        if page.url and _social_post_platform(page.url):
            image_url = next(
                (
                    img.url
                    for img in (*page.full_matching_images, *page.partial_matching_images)
                    if img.url
                ),
                None,
            )
            if image_url and _live_social_post(page.url):
                matches.append(
                    {
                        "url": page.url,
                        "compared_image_url": image_url,
                        "match_type": "social_post",
                        "page_url": page.url,
                    }
                )
        if not page.full_matching_images and not page.partial_matching_images:
            _add_image(matches, page.url, "full_page_match", page.url)

    for img in web.full_matching_images:
        _add_image(matches, img.url, "full_image_match")

    for img in web.partial_matching_images:
        _add_image(matches, img.url, "partial_image_match")

    for img in web.visually_similar_images:
        _add_image(matches, img.url, "visually_similar")

    return matches


def _commons_matches(image_path: str) -> list[dict]:
    with open(image_path, "rb") as f:
        sha1 = hashlib.sha1(f.read()).hexdigest()

    response = _SESSION.get(
        _COMMONS_API,
        params={
            "action": "query",
            "format": "json",
            "list": "allimages",
            "aisha1": sha1,
            "aiprop": "url|canonicaltitle",
        },
        timeout=30,
    )
    response.raise_for_status()
    images = response.json().get("query", {}).get("allimages", [])

    matches = []
    for img in images:
        file_url = img.get("url")
        page_url = img.get("descriptionurl") or file_url
        title = img.get("canonicaltitle") or img.get("name")
        if not file_url and not page_url:
            continue
        matches.append(
            {
                "url": file_url or page_url,
                "match_type": "full_image_match",
                "page_url": page_url,
                "page_title": title,
                "platform": "wikimedia",
            }
        )
    return matches


def _mime_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/jpeg")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    if "lookaside.instagram.com" in host:
        return False
    if "google_widget" in path or "google_widget" in query:
        return False
    return True


def _dedupe(matches: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for match in matches:
        url = (match.get("url") or "").split("#")[0].rstrip("/")
        if not url or url in seen or not _is_http_url(url):
            continue
        seen.add(url)
        unique.append(match)
    return unique


def _rank(matches: list[dict], limit: int = TOP_N) -> list[dict]:
    annotated = [_annotate(dict(match)) for match in _dedupe(matches)]
    annotated.sort(key=lambda item: item.get("score") or 0, reverse=True)

    picked: list[dict] = []
    seen_urls: set[str] = set()
    seen_platforms: set[str] = set()

    def _take(match: dict) -> None:
        url = match.get("url")
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        picked.append(match)

    for platform in _PREFERRED_PLATFORMS:
        if len(picked) >= limit:
            break
        for match in annotated:
            if match.get("platform") == platform and platform not in seen_platforms:
                _take(match)
                seen_platforms.add(platform)
                break

    for match in annotated:
        if len(picked) >= limit:
            break
        _take(match)

    return picked[:limit]


def _yandex_matches(image_path: str) -> list[dict]:
    with open(image_path, "rb") as f:
        response = _SESSION.post(
            "https://yandex.com/images/search",
            params={
                "rpt": "imageview",
                "format": "json",
                "request": '{"blocks":[{"block":"b-page_type_search-by-image__link"}]}',
            },
            files={"upfile": (Path(image_path).name, f, _mime_for(image_path))},
            timeout=60,
        )
    response.raise_for_status()
    payload = response.json()
    cbir_url = payload["blocks"][0]["params"]["url"]
    results_url = urljoin("https://yandex.com/images/search", cbir_url)

    html = _SESSION.get(results_url, timeout=60).text
    matches = []

    for raw in re.findall(r"https?://[^\s\"'<>]+", html):
        url = raw.rstrip("\\,);")
        host = urlparse(url).netloc.lower()
        if any(skip in host for skip in ("yandex.", "yastatic.", "yandex-team.")):
            continue
        if any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            matches.append({"url": url.split("?")[0], "match_type": "full_image_match"})
        elif "/wiki" in url or host.startswith("en.wikipedia") or "news" in host or "blog" in host:
            matches.append({"url": url.split("?")[0], "match_type": "full_page_match"})

    for raw in re.findall(r'data-url="(https?://[^"]+)"', html):
        host = urlparse(raw).netloc.lower()
        if "yandex." in host:
            continue
        matches.append({"url": raw, "match_type": "full_page_match"})

    return _dedupe(matches)


def _bing_matches(image_path: str) -> list[dict]:
    with open(image_path, "rb") as f:
        upload = _SESSION.post(
            "https://www.bing.com/images/search?view=detailv2&iss=sbiupload",
            files={"imageBin": (Path(image_path).name, f, _mime_for(image_path))},
            timeout=60,
            allow_redirects=True,
        )
    upload.raise_for_status()
    html = upload.text
    matches = []

    for key in ("pageUrl", "purl", "hostPageUrl", "murl", "cdnUrl"):
        for raw in re.findall(rf'"{key}"\s*:\s*"(https?:[^"]+)"', html):
            url = raw.encode("utf-8").decode("unicode_escape")
            host = urlparse(url).netloc.lower()
            if "bing.com" in host or "microsoft.com" in host:
                continue
            match_type = (
                "full_image_match"
                if any(url.lower().split("?")[0].endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
                else "full_page_match"
            )
            matches.append({"url": url, "match_type": match_type})

    return _dedupe(matches)


def find_matching_posts(image_path: str, limit: int = TOP_N) -> list[dict]:
    """Find live social-media posts whose images match the supplied pixels."""
    collected: list[dict] = []

    try:
        vision_matches = _vision_visual_matches(image_path)
        collected.extend(vision_matches)
        if vision_matches:
            print(f"Found {len(vision_matches)} visually similar image(s) via reverse image search.")
        else:
            print("Google Vision returned no similar images; trying other search backends.")
    except (GoogleAPICallError, RuntimeError, OSError) as exc:
        print(f"Google Vision unavailable ({exc.__class__.__name__}); trying other search backends.")

    social_matches = [
        item for item in collected
        if item.get("match_type") == "social_post"
    ]
    if social_matches:
        print(f"Found {len(social_matches)} live social-media post(s) from reverse image search.")
        return _rank(social_matches, min(limit, len(social_matches)))

    try:
        collected.extend(_commons_matches(image_path))
    except (requests.RequestException, ValueError):
        pass

    ranked = _rank(collected, limit)

    try:
        extra = _yandex_matches(image_path)
        extra = [item for item in extra if item.get("match_type") == "full_image_match"]
        if extra:
            print("Filled remaining slots via reverse image search.")
            collected.extend(extra)
            ranked = _rank(collected, limit)
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Yandex reverse search skipped ({exc.__class__.__name__}).")

    try:
        extra = _bing_matches(image_path)
        extra = [item for item in extra if item.get("match_type") == "full_image_match"]
        if extra:
            print("Filled remaining slots via Bing visual search.")
            collected.extend(extra)
            ranked = _rank(collected, limit)
    except (requests.RequestException, ValueError) as exc:
        print(f"Bing reverse search skipped ({exc.__class__.__name__}).")

    social_matches = [
        item for item in collected
        if item.get("match_type") == "social_post"
    ]
    if social_matches:
        return _rank(social_matches, min(limit, len(social_matches)))

    print("No live social-media post was found by the reverse-image providers.")
    return []


if __name__ == "__main__":
    import sys

    results = find_matching_posts(sys.argv[1])
    print(json.dumps(results, indent=2))

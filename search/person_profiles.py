"""Resolve a named face to Wikipedia and public social profiles via Wikidata."""

from __future__ import annotations

from urllib.parse import quote, urlparse

import requests

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"

_HUMAN_INSTANCE_IDS = {
    "Q5",  # human
    "Q15632617",  # fictional human
    "Q21070568",  # human who may be fictional
}

_SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
}

# Public identifier properties on Wikidata person items.
_SOCIAL_PROPS = {
    "P2002": ("twitter", "https://x.com/{id}"),
    "P2003": ("instagram", "https://www.instagram.com/{id}/"),
    "P2013": ("facebook", "https://www.facebook.com/{id}"),
    "P2397": ("youtube", "https://www.youtube.com/channel/{id}"),
    "P4264": ("linkedin", "https://www.linkedin.com/company/{id}"),
    "P6634": ("linkedin", None),  # personal profile id
    "P7085": ("tiktok", "https://www.tiktok.com/@{id}"),
    "P3267": ("flickr", "https://www.flickr.com/people/{id}"),
    "P856": ("official", None),  # official website, already a URL
}


def _linkedin_personal_url(profile_id: str) -> str:
    value = profile_id.strip().lstrip("/")
    if value.startswith(("in/", "pub/", "company/")):
        return f"https://www.linkedin.com/{value}"
    return f"https://www.linkedin.com/in/{value}"


def _claim_strings(entity: dict, prop: str) -> list[str]:
    values = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak") or {}
        datavalue = snak.get("datavalue") or {}
        value = datavalue.get("value")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, dict) and value.get("id"):
            values.append(str(value["id"]))
    return values


def _profile(
    url: str,
    *,
    platform: str,
    title: str,
    name: str,
    match_type: str = "named_profile",
) -> dict:
    return {
        "url": url,
        "match_type": match_type,
        "page_title": title,
        "platform": platform,
        "person_name": name,
    }


def _wiki_title_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,:'")


def _is_human_item(entity: dict) -> bool:
    return any(qid in _HUMAN_INSTANCE_IDS for qid in _claim_strings(entity, "P31"))


def lookup_person_profiles(name: str, session: requests.Session) -> list[dict]:
    """Return Wikipedia / Commons / social profile URLs for a detected person name."""
    if not name or len(name.split()) < 2:
        return []

    entity_id, label = _wikidata_search(name, session)
    if not entity_id:
        entity_id, label, wiki_title = _wikipedia_to_wikidata(name, session)
        if wiki_title and not entity_id:
            return []
        if not entity_id:
            return []
    else:
        label = label or name

    matches: list[dict] = [
        _profile(
            f"https://www.wikidata.org/wiki/{entity_id}",
            platform="wikidata",
            title=f"{label} (Wikidata)",
            name=label,
        )
    ]

    entity_resp = session.get(
        _WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "ids": entity_id,
            "props": "claims|sitelinks",
            "languages": "en",
            "format": "json",
        },
        timeout=20,
    )
    entity_resp.raise_for_status()
    entity = entity_resp.json().get("entities", {}).get(entity_id) or {}
    if not _is_human_item(entity):
        print(f"Skipping profile lookup: '{label}' is not a person in Wikidata.")
        return []

    enwiki = (entity.get("sitelinks") or {}).get("enwiki", {}).get("title")
    if enwiki:
        matches.append(
            _profile(_wiki_title_url(enwiki), platform="wikipedia", title=enwiki, name=label)
        )
        matches.extend(_wikipedia_extlinks(enwiki, label, session))
        matches.extend(_wikipedia_pageimage(enwiki, label, session))

    commons_title = (entity.get("sitelinks") or {}).get("commonswiki", {}).get("title")
    if commons_title:
        matches.append(
            _profile(
                "https://commons.wikimedia.org/wiki/" + quote(commons_title.replace(" ", "_")),
                platform="wikimedia",
                title=commons_title,
                name=label,
            )
        )

    for prop, (platform, template) in _SOCIAL_PROPS.items():
        for raw in _claim_strings(entity, prop):
            if prop == "P856":
                url = raw
            elif prop == "P6634":
                url = _linkedin_personal_url(raw)
            else:
                url = template.format(id=raw)
            matches.append(
                _profile(url, platform=platform, title=f"{label} on {platform}", name=label)
            )

    matches.extend(_commons_file_hits(label, session))
    return matches


def _wikidata_search(name: str, session: requests.Session) -> tuple[str | None, str | None]:
    search = session.get(
        _WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": 1,
            "format": "json",
        },
        timeout=20,
    )
    search.raise_for_status()
    hits = search.json().get("search") or []
    if not hits:
        return None, None
    return hits[0]["id"], hits[0].get("label") or name


def _wikipedia_to_wikidata(
    name: str, session: requests.Session
) -> tuple[str | None, str | None, str | None]:
    response = session.get(
        _WIKI_API,
        params={
            "action": "opensearch",
            "search": name,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    titles = payload[1] if len(payload) > 1 else []
    if not titles:
        return None, None, None
    title = titles[0]
    props = session.get(
        _WIKI_API,
        params={
            "action": "query",
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "titles": title,
            "format": "json",
        },
        timeout=20,
    )
    props.raise_for_status()
    pages = props.json().get("query", {}).get("pages", {})
    for page in pages.values():
        item = (page.get("pageprops") or {}).get("wikibase_item")
        if item:
            return item, title, title
    return None, title, title


def _host_platform(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for suffix, name in _SOCIAL_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return None


def _wikipedia_extlinks(title: str, name: str, session: requests.Session) -> list[dict]:
    response = session.get(
        _WIKI_API,
        params={
            "action": "query",
            "prop": "extlinks",
            "titles": title,
            "ellimit": 500,
            "format": "json",
        },
        timeout=20,
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    found: list[dict] = []
    seen_platforms: set[str] = set()
    for page in pages.values():
        for item in page.get("extlinks") or []:
            url = item.get("*") or item.get("url") or ""
            platform = _host_platform(url)
            if not platform or platform in seen_platforms:
                continue
            if platform == "youtube" and ("watch?" in url.lower() or "/shorts/" in url.lower()):
                continue
            if platform == "twitter" and "/status/" in url.lower():
                continue
            seen_platforms.add(platform)
            found.append(
                _profile(url, platform=platform, title=f"{name} on {platform}", name=name)
            )
    return found


def _wikipedia_pageimage(title: str, name: str, session: requests.Session) -> list[dict]:
    response = session.get(
        _WIKI_API,
        params={
            "action": "query",
            "prop": "pageimages",
            "piprop": "original|name",
            "titles": title,
            "format": "json",
        },
        timeout=20,
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    hits = []
    for page in pages.values():
        original = (page.get("original") or {}).get("source")
        file_name = page.get("pageimage")
        if original:
            hits.append(
                {
                    "url": original,
                    "match_type": "full_image_match",
                    "page_title": file_name or f"{name} Wikipedia portrait",
                    "platform": "wikipedia",
                    "person_name": name,
                }
            )
        if file_name:
            hits.append(
                _profile(
                    "https://en.wikipedia.org/wiki/File:" + quote(file_name.replace(" ", "_")),
                    platform="wikipedia",
                    title=f"File:{file_name}",
                    name=name,
                )
            )
    return hits


def _commons_file_hits(name: str, session: requests.Session) -> list[dict]:
    response = session.get(
        _COMMONS_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": name,
            "srnamespace": 6,
            "srlimit": 3,
            "format": "json",
        },
        timeout=20,
    )
    response.raise_for_status()
    hits = []
    for item in response.json().get("query", {}).get("search", []):
        title = item.get("title")
        if not title:
            continue
        hits.append(
            _profile(
                "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_")),
                platform="wikimedia",
                title=title,
                name=name,
            )
        )
    return hits

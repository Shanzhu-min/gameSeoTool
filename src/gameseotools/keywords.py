from __future__ import annotations

import re

from .models import GamePage, KeywordCandidate


TAIL_MODIFIERS = {
    "game",
    "games",
    "online",
    "play",
    "free",
    "unblocked",
    "poki",
    "crazygames",
    "y8",
    "y8com",
    "coolmath",
    "crazy",
}


def generate_keywords(page: GamePage, max_keywords: int = 4) -> list[KeywordCandidate]:
    base = normalize_keyword(page.title)
    if not base:
        return []
    canonical = canonical_keyword(base, site_name=page.site_name)

    variants = [
        canonical,
        f"{canonical} game",
        f"{canonical} online",
        f"{canonical} {page.site_name}",
        f"{canonical} unblocked",
    ]
    cleaned: list[str] = []
    for item in variants:
        keyword = normalize_keyword(item)
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)

    return [
        KeywordCandidate(
            keyword=keyword,
            canonical_keyword=canonical,
            game_url=page.url,
            site_name=page.site_name,
            source="sitemap",
        )
        for keyword in cleaned[:max_keywords]
    ]


def normalize_keyword(value: str) -> str:
    value = value.lower()
    value = value.replace(".io", " io")
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def canonical_keyword(value: str, site_name: str | None = None) -> str:
    words = normalize_keyword(value).split()
    modifiers = set(TAIL_MODIFIERS)
    if site_name:
        modifiers.add(normalize_keyword(site_name).replace(" ", ""))
        modifiers.add(normalize_keyword(site_name))
    while len(words) > 1 and words[-1] in modifiers:
        words.pop()
    return " ".join(words)

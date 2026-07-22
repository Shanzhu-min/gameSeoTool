from __future__ import annotations

import re

from .models import GamePage, KeywordCandidate


def generate_keywords(page: GamePage, max_keywords: int = 4) -> list[KeywordCandidate]:
    base = normalize_keyword(page.title)
    if not base:
        return []

    variants = [
        base,
        f"{base} game",
        f"{base} online",
        f"{base} {page.site_name}",
        f"{base} unblocked",
    ]
    cleaned: list[str] = []
    for item in variants:
        keyword = normalize_keyword(item)
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)

    return [
        KeywordCandidate(keyword=keyword, game_url=page.url, site_name=page.site_name, source="sitemap")
        for keyword in cleaned[:max_keywords]
    ]


def normalize_keyword(value: str) -> str:
    value = value.lower()
    value = value.replace(".io", " io")
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

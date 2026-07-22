from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import quote_plus

from .keywords import canonical_keyword


@dataclass(frozen=True)
class TrendMetrics:
    first: int | None
    last: int | None
    peak: int | None
    recent_avg: float | None
    previous_avg: float | None
    growth_pct: float | None
    validation_status: str
    recommendation: str


def compute_trend_metrics(values: list[int]) -> TrendMetrics:
    if not values:
        return TrendMetrics(None, None, None, None, None, None, "no_trend_data", "low")
    first = values[0]
    last = values[-1]
    peak = max(values)
    recent = values[-7:] if len(values) >= 7 else values
    previous = values[:-7] if len(values) >= 14 else values[: max(1, len(values) // 2)]
    recent_avg = sum(recent) / len(recent)
    previous_avg = sum(previous) / len(previous) if previous else 0
    growth_pct = ((recent_avg - previous_avg) / previous_avg * 100) if previous_avg else None

    if growth_pct is not None and growth_pct >= 50 and last >= 20:
        validation_status = "passed"
        recommendation = "high"
    elif growth_pct is not None and growth_pct >= 15:
        validation_status = "watch_growth"
        recommendation = "medium"
    elif peak >= 50 and last < first:
        validation_status = "old_or_declining"
        recommendation = "low"
    elif peak >= 50:
        validation_status = "volume_without_growth"
        recommendation = "medium"
    else:
        validation_status = "weak_signal"
        recommendation = "low"

    return TrendMetrics(
        first=first,
        last=last,
        peak=peak,
        recent_avg=round(recent_avg, 2),
        previous_avg=round(previous_avg, 2) if previous else None,
        growth_pct=round(growth_pct, 2) if growth_pct is not None else None,
        validation_status=validation_status,
        recommendation=recommendation,
    )


def trends_url(keyword: str, baseline: str = "GPTs") -> str:
    query = quote_plus(f"{keyword},{baseline}")
    return f"https://trends.google.com/trends/explore?date=today%201-m&q={query}"


def search_url(keyword: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(keyword)}"


def wiki_search_url(keyword: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(keyword + ' wiki')}"


def youtube_search_url(keyword: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"


def roblox_search_url(keyword: str) -> str:
    return f"https://www.roblox.com/discover#/?Keyword={quote_plus(keyword)}"


def domain_candidates(keyword: str) -> list[tuple[str, str]]:
    base = canonical_keyword(keyword).replace(" ", "")
    dashed = canonical_keyword(keyword).replace(" ", "-")
    names = [
        f"{base}.wiki",
        f"{base}.com",
        f"{base}.org",
        f"{base}.net",
        f"{dashed}.wiki",
        f"{dashed}.com",
        f"{dashed}.org",
        f"{dashed}.net",
    ]
    results: list[tuple[str, str]] = []
    for name in dict.fromkeys(names):
        results.append((name, dns_status(name)))
    return results


def dns_status(domain: str) -> str:
    try:
        socket.getaddrinfo(domain, None)
        return "dns_active"
    except OSError:
        return "dns_unresolved"

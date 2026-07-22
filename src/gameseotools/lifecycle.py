from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .reporting import compute_trend_metrics


ACTIVE_STATUSES = {"new_candidate", "watching", "recommended"}
INACTIVE_STATUSES = {"old_game", "noise", "archived"}
GENERIC_WORDS = {
    "game",
    "games",
    "online",
    "play",
    "free",
    "unblocked",
    "car",
    "cars",
    "bike",
    "basketball",
    "football",
    "shooting",
}


def evidence_score_for_candidate(row: Any, now: datetime | None = None) -> tuple[int, list[str]]:
    """Score whether a canonical keyword is worth trend validation.

    This is intentionally conservative and cheap: it only uses data we already
    have before making external trend requests.
    """

    now = now or datetime.now(timezone.utc)
    status = str(row_get(row, "lifecycle_status", "new_candidate") or "new_candidate")
    keyword = str(row_get(row, "keyword", "") or row_get(row, "canonical_keyword", "") or "")
    if status in INACTIVE_STATUSES:
        return 0, [f"Lifecycle status is {status}: excluded"]

    score = 10
    reasons = ["Base candidate: +10"]

    first_seen = parse_datetime(row_get(row, "first_seen_at"))
    if first_seen:
        age_days = max(0, (now - first_seen).days)
        if age_days <= 7:
            score += 20
            reasons.append("First seen within 7 days: +20")
        elif age_days <= 30:
            score += 10
            reasons.append("First seen within 30 days: +10")
        else:
            score -= 10
            reasons.append("First seen over 30 days ago: -10")

    source_count = safe_int(row_get(row, "source_count"), 1)
    if source_count >= 2:
        score += 10
        reasons.append("Seen on multiple source sites: +10")

    variant_count = safe_int(row_get(row, "variant_count"), 1)
    if variant_count >= 3:
        score += 8
        reasons.append("Multiple keyword variants found: +8")
    elif variant_count >= 2:
        score += 5
        reasons.append("At least two keyword variants found: +5")

    site_name = str(row_get(row, "site_name", "") or "").lower()
    if site_name in {"crazygames", "poki"}:
        score += 7
        reasons.append(f"Source site {site_name}: +7")
    elif site_name == "y8":
        score += 3
        reasons.append("Source site y8: +3")

    words = keyword.split()
    if 2 <= len(words) <= 6:
        score += 8
        reasons.append("Keyword length looks like a game title: +8")
    elif len(words) <= 1:
        score -= 12
        reasons.append("Keyword is very short and may be generic: -12")

    if words and all(word in GENERIC_WORDS for word in words):
        score -= 25
        reasons.append("Keyword appears generic: -25")
    elif words and words[-1] in GENERIC_WORDS and len(words) <= 2:
        score -= 10
        reasons.append("Keyword has generic game wording: -10")

    drop_count = safe_int(row_get(row, "drop_count"), 0)
    if drop_count >= 2:
        score -= 20
        reasons.append("Dropped repeatedly before: -20")
    elif drop_count == 1:
        score -= 8
        reasons.append("Dropped once before: -8")

    return max(0, min(100, score)), reasons


def lifecycle_after_score(score_result: Any, graph_values: list[int]) -> tuple[str, int | None, str]:
    metrics = compute_trend_metrics(graph_values)
    status = str(getattr(score_result, "status", "drop"))
    is_noise = bool(getattr(score_result, "is_noise", False))

    if is_noise:
        return "noise", None, "Marked as noise by intent analysis"

    if status == "push":
        return "recommended", 14, "Recommended in report; cooling down to avoid duplicate recommendations"

    if metrics.validation_status == "old_or_declining":
        return "old_game", 30, "Trend validation says old_or_declining"

    if metrics.validation_status == "no_trend_data":
        return "watching", 7, "No trend data; wait before retrying"

    if status == "observe":
        return "watching", 3, "Some signal found; watch again later"

    if metrics.validation_status in {"weak_signal", "volume_without_growth"}:
        return "watching", 7, f"Validation status is {metrics.validation_status}"

    return "archived", 14, "Low opportunity score; archived for now"


def cooldown_until(days: int | None, now: datetime | None = None) -> str | None:
    if days is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=days)).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

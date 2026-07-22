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
    """计算 canonical keyword 是否值得进入趋势验证。

    这里故意保持保守和低成本：只使用外部趋势请求之前已经掌握的数据。
    """

    now = now or datetime.now(timezone.utc)
    status = str(row_get(row, "lifecycle_status", "new_candidate") or "new_candidate")
    keyword = str(row_get(row, "keyword", "") or row_get(row, "canonical_keyword", "") or "")
    if status in INACTIVE_STATUSES:
        return 0, [f"生命周期状态为 {status}：排除"]

    score = 10
    reasons = ["基础候选词：+10"]

    first_seen = parse_datetime(row_get(row, "first_seen_at"))
    if first_seen:
        age_days = max(0, (now - first_seen).days)
        if age_days <= 7:
            score += 20
            reasons.append("首次发现时间在 7 天内：+20")
        elif age_days <= 30:
            score += 10
            reasons.append("首次发现时间在 30 天内：+10")
        else:
            score -= 10
            reasons.append("首次发现已超过 30 天：-10")

    source_count = safe_int(row_get(row, "source_count"), 1)
    if source_count >= 2:
        score += 10
        reasons.append("出现在多个来源站点：+10")

    variant_count = safe_int(row_get(row, "variant_count"), 1)
    if variant_count >= 3:
        score += 8
        reasons.append("发现多个关键词变体：+8")
    elif variant_count >= 2:
        score += 5
        reasons.append("至少发现两个关键词变体：+5")

    site_name = str(row_get(row, "site_name", "") or "").lower()
    if site_name in {"crazygames", "poki"}:
        score += 7
        reasons.append(f"来源站点 {site_name}：+7")
    elif site_name == "y8":
        score += 3
        reasons.append("来源站点 y8：+3")

    words = keyword.split()
    if 2 <= len(words) <= 6:
        score += 8
        reasons.append("关键词长度较像游戏标题：+8")
    elif len(words) <= 1:
        score -= 12
        reasons.append("关键词过短，可能偏泛：-12")

    if words and all(word in GENERIC_WORDS for word in words):
        score -= 25
        reasons.append("关键词看起来偏泛：-25")
    elif words and words[-1] in GENERIC_WORDS and len(words) <= 2:
        score -= 10
        reasons.append("关键词包含较泛的游戏表达：-10")

    drop_count = safe_int(row_get(row, "drop_count"), 0)
    if drop_count >= 2:
        score -= 20
        reasons.append("此前多次被丢弃：-20")
    elif drop_count == 1:
        score -= 8
        reasons.append("此前被丢弃过一次：-8")

    return max(0, min(100, score)), reasons


def lifecycle_after_score(score_result: Any, graph_values: list[int]) -> tuple[str, int | None, str]:
    metrics = compute_trend_metrics(graph_values)
    status = str(getattr(score_result, "status", "drop"))
    is_noise = bool(getattr(score_result, "is_noise", False))

    if is_noise:
        return "noise", None, "意图分析判断为噪音词"

    if status == "push":
        return "recommended", 14, "已进入推荐报告，进入冷却期以避免重复推荐"

    if metrics.validation_status == "old_or_declining":
        return "old_game", 30, "趋势验证结果为 old_or_declining"

    if metrics.validation_status == "no_trend_data":
        return "watching", 7, "暂无趋势数据，等待一段时间后再复查"

    if status == "observe":
        return "watching", 3, "存在部分信号，稍后继续观察"

    if metrics.validation_status in {"weak_signal", "volume_without_growth"}:
        return "watching", 7, f"趋势验证状态为 {metrics.validation_status}"

    return "archived", 14, "机会分较低，暂时归档"


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

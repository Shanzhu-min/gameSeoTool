from __future__ import annotations

from .models import ScoreResult, TrendResult
from .reporting import compute_trend_metrics


def score_keyword(
    keyword: str,
    trend: TrendResult,
    intent_summary: str,
    is_game_related: bool,
    is_noise: bool,
    was_pushed: bool,
    push_threshold: int,
    observe_threshold: int,
    evidence_score: int = 0,
) -> ScoreResult:
    score = 15
    reasons = ["关键词候选项：+15"]

    values = trend.graph_values
    metrics = compute_trend_metrics(values)
    if values:
        recent = values[-7:] if len(values) >= 7 else values
        previous = values[:-7] if len(values) >= 14 else values[: max(1, len(values) // 2)]
        recent_avg = sum(recent) / len(recent)
        previous_avg = sum(previous) / len(previous) if previous else 0
        first_value = values[0]
        last_value = values[-1]
        peak = max(values)
        growth = recent_avg - previous_avg
        if previous_avg > 0 and recent_avg >= previous_avg * 1.25:
            score += 30
            reasons.append("近期趋势均值增长 25% 以上：+30")
        elif growth > 5:
            score += 15
            reasons.append("近期趋势均值有中等增长：+15")
        elif recent_avg < previous_avg * 0.85:
            score -= 20
            reasons.append("近期趋势正在下滑：-20")

        if last_value >= max(20, first_value + 10):
            score += 20
            reasons.append("末值明显高于首值：+20")
        elif last_value <= max(5, first_value - 10):
            score -= 10
            reasons.append("末值低于首值：-10")

        if peak >= 50 and recent_avg >= 20:
            score += 10
            reasons.append("当前仍有较明显趋势热度：+10")
        elif peak >= 50:
            score += 5
            reasons.append("历史峰值达到 50+，但当前信号偏弱：+5")
    else:
        score -= 20
        reasons.append("没有趋势曲线数据：-20")

    if trend.related_rising:
        score += 30
        reasons.append("存在上升相关查询：+30")
    else:
        score -= 10
        reasons.append("没有上升相关查询：-10")

    if is_game_related:
        score += 15
        reasons.append("具备游戏相关意图：+15")
    else:
        score -= 10
        reasons.append("未识别到明确游戏意图：-10")

    if is_noise:
        score -= 50
        reasons.append("疑似非游戏噪音词：-50")

    if was_pushed:
        score -= 100
        reasons.append("此前已经推送过：-100")

    if metrics.validation_status == "no_trend_data":
        score = min(score, observe_threshold - 1)
        reasons.append("趋势验证门槛：没有趋势数据，不能进入观察或推荐")
    elif metrics.validation_status == "old_or_declining":
        score = min(score, observe_threshold)
        reasons.append("趋势验证门槛：老词或下滑趋势，不能推荐")
    elif metrics.validation_status == "volume_without_growth":
        score = min(score, push_threshold - 1)
        reasons.append("趋势验证门槛：有热度但缺少增长，不能推荐")
    elif metrics.validation_status == "weak_signal":
        score = min(score, observe_threshold)
        reasons.append("趋势验证门槛：信号偏弱，不能推荐")

    trend_score = max(0, min(100, score))
    if evidence_score > 0:
        score = round((trend_score * 0.65) + (evidence_score * 0.35))
        reasons.append(f"综合趋势分 {trend_score} 与证据分 {evidence_score}")
    else:
        score = trend_score
    if metrics.validation_status == "no_trend_data":
        score = min(score, observe_threshold - 1)
    elif metrics.validation_status == "old_or_declining":
        score = min(score, observe_threshold)
    elif metrics.validation_status == "volume_without_growth":
        score = min(score, push_threshold - 1)
    elif metrics.validation_status == "weak_signal":
        score = min(score, observe_threshold)

    if score >= push_threshold:
        status = "push"
    elif score >= observe_threshold:
        status = "observe"
    else:
        status = "drop"

    return ScoreResult(
        keyword=keyword,
        score=score,
        status=status,
        reasons=reasons,
        intent_summary=intent_summary,
        is_noise=is_noise,
        evidence_score=evidence_score,
    )

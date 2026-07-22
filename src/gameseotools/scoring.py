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
    reasons = ["Keyword candidate: +15"]

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
            reasons.append("Recent trend average increased by 25%+: +30")
        elif growth > 5:
            score += 15
            reasons.append("Recent trend average increased moderately: +15")
        elif recent_avg < previous_avg * 0.85:
            score -= 20
            reasons.append("Recent trend is declining: -20")

        if last_value >= max(20, first_value + 10):
            score += 20
            reasons.append("Last value is meaningfully above first value: +20")
        elif last_value <= max(5, first_value - 10):
            score -= 10
            reasons.append("Last value is below first value: -10")

        if peak >= 50 and recent_avg >= 20:
            score += 10
            reasons.append("Trend has meaningful current volume: +10")
        elif peak >= 50:
            score += 5
            reasons.append("Historical peak reached 50+, but current signal is weak: +5")
    else:
        score -= 20
        reasons.append("No trend curve data: -20")

    if trend.related_rising:
        score += 30
        reasons.append("Has rising related queries: +30")
    else:
        score -= 10
        reasons.append("No rising related queries: -10")

    if is_game_related:
        score += 15
        reasons.append("Game-related intent: +15")
    else:
        score -= 10
        reasons.append("No clear game intent: -10")

    if is_noise:
        score -= 50
        reasons.append("Likely non-game noise: -50")

    if was_pushed:
        score -= 100
        reasons.append("Already pushed: -100")

    if metrics.validation_status == "no_trend_data":
        score = min(score, observe_threshold - 1)
        reasons.append("Validation gate: no trend data cannot be observed or pushed")
    elif metrics.validation_status == "old_or_declining":
        score = min(score, observe_threshold)
        reasons.append("Validation gate: old or declining trend cannot be pushed")
    elif metrics.validation_status == "volume_without_growth":
        score = min(score, push_threshold - 1)
        reasons.append("Validation gate: volume without growth cannot be pushed")
    elif metrics.validation_status == "weak_signal":
        score = min(score, observe_threshold)
        reasons.append("Validation gate: weak signal cannot be pushed")

    trend_score = max(0, min(100, score))
    if evidence_score > 0:
        score = round((trend_score * 0.65) + (evidence_score * 0.35))
        reasons.append(f"Blended trend score {trend_score} with evidence score {evidence_score}")
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

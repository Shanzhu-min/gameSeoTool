from __future__ import annotations

from .models import ScoreResult, TrendResult


def score_keyword(
    keyword: str,
    trend: TrendResult,
    intent_summary: str,
    is_game_related: bool,
    is_noise: bool,
    was_pushed: bool,
    push_threshold: int,
    observe_threshold: int,
) -> ScoreResult:
    score = 20
    reasons = ["Recent keyword candidate: +20"]

    values = trend.graph_values
    if values:
        recent = values[-7:] if len(values) >= 7 else values
        previous = values[:-7] if len(values) >= 14 else values[: max(1, len(values) // 2)]
        recent_avg = sum(recent) / len(recent)
        previous_avg = sum(previous) / len(previous) if previous else 0
        if recent_avg > previous_avg:
            score += 20
            reasons.append("Recent trend average increased: +20")
        if max(values) >= 50:
            score += 20
            reasons.append("Trend peak reached 50+: +20")
    else:
        reasons.append("No trend curve data: +0")

    if trend.related_rising:
        score += 20
        reasons.append("Has rising related queries: +20")

    if is_game_related:
        score += 20
        reasons.append("Game-related intent: +20")

    if is_noise:
        score -= 50
        reasons.append("Likely non-game noise: -50")

    if was_pushed:
        score -= 100
        reasons.append("Already pushed: -100")

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
    )

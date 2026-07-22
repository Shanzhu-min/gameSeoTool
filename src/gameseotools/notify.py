from __future__ import annotations

import os
from typing import Protocol

from .http import post_json
from .models import ScoreResult, TrendResult


class Notifier(Protocol):
    channel: str

    def send(self, score: ScoreResult, trend: TrendResult, game_url: str, site_name: str) -> tuple[bool, dict, str]:
        ...


class WebhookNotifier:
    def __init__(self, channel: str, webhook_url: str):
        self.channel = channel
        self.webhook_url = webhook_url

    def send(self, score: ScoreResult, trend: TrendResult, game_url: str, site_name: str) -> tuple[bool, dict, str]:
        text = build_message(score, trend, game_url, site_name)
        if self.channel == "wecom":
            payload = {"msgtype": "markdown", "markdown": {"content": text}}
        else:
            payload = {"msg_type": "text", "content": {"text": text}}
        try:
            response = post_json(self.webhook_url, payload, timeout=30)
            return True, payload, str(response)
        except Exception as exc:
            return False, payload, str(exc)


def configured_notifiers() -> list[WebhookNotifier]:
    notifiers: list[WebhookNotifier] = []
    feishu = os.getenv("FEISHU_WEBHOOK_URL")
    wecom = os.getenv("WECOM_WEBHOOK_URL")
    if feishu:
        notifiers.append(WebhookNotifier("feishu", feishu))
    if wecom:
        notifiers.append(WebhookNotifier("wecom", wecom))
    return notifiers


def build_message(score: ScoreResult, trend: TrendResult, game_url: str, site_name: str) -> str:
    rising = "\n".join(f"- {query}: {value}" for query, value in trend.related_rising[:8]) or "- 暂无"
    top = "\n".join(f"- {query}" for query in trend.related_top[:8]) or "- 暂无"
    reasons = "\n".join(f"- {reason}" for reason in score.reasons)
    peak = max(trend.graph_values) if trend.graph_values else "暂无"
    return (
        f"发现新趋势词：{score.keyword}\n\n"
        f"来源站点：{site_name}\n"
        f"游戏页面：{game_url}\n"
        f"机会分：{score.score}\n"
        f"处理状态：{score.status}\n"
        f"趋势峰值：{peak}\n\n"
        f"评分原因：\n{reasons}\n\n"
        f"搜索意图：\n{score.intent_summary}\n\n"
        f"Rising related queries：\n{rising}\n\n"
        f"Top related queries：\n{top}\n"
    )

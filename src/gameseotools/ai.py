from __future__ import annotations

import os
from typing import Any

from .http import post_json
from .models import TrendResult


NOISE_HINTS = {
    "album",
    "song",
    "lyrics",
    "movie",
    "film",
    "actor",
    "actress",
    "election",
    "stock",
}

GAME_HINTS = {
    "game",
    "games",
    "play",
    "online",
    "unblocked",
    "poki",
    "crazygames",
    "y8",
    "io",
    "roblox",
    "steam",
}


class IntentAnalyzer:
    def analyze(self, keyword: str, trend: TrendResult) -> tuple[str, bool, bool]:
        raise NotImplementedError


class RuleIntentAnalyzer(IntentAnalyzer):
    def analyze(self, keyword: str, trend: TrendResult) -> tuple[str, bool, bool]:
        terms = " ".join([keyword, *trend.related_top, *[query for query, _ in trend.related_rising]]).lower()
        is_noise = any(hint in terms.split() for hint in NOISE_HINTS)
        is_game = any(hint in terms.split() for hint in GAME_HINTS)
        if is_game and not is_noise:
            summary = "Rule check: this keyword includes game, platform, or online play intent and is worth SEO monitoring."
        elif is_noise:
            summary = "Rule check: this keyword looks like non-game noise, such as music, film, news, or celebrity intent."
        else:
            summary = "Rule check: no clear game intent was detected, so keep it in low-priority observation."
        return summary, is_game, is_noise


class OpenAIIntentAnalyzer(IntentAnalyzer):
    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.fallback = RuleIntentAnalyzer()

    @classmethod
    def from_env(cls) -> "OpenAIIntentAnalyzer | None":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return cls(api_key=api_key, model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))

    def analyze(self, keyword: str, trend: TrendResult) -> tuple[str, bool, bool]:
        prompt = {
            "keyword": keyword,
            "related_top": trend.related_top[:10],
            "related_rising": trend.related_rising[:10],
            "task": "判断这个关键词是否适合小游戏/在线游戏 SEO 机会监控。返回 JSON：summary, is_game_related, is_noise。",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是游戏 SEO 趋势分析助手。只输出严格 JSON。"},
                {"role": "user", "content": str(prompt)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            response = post_json(
                self.endpoint,
                payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60,
            )
            content = response["choices"][0]["message"]["content"]
            import json

            data: dict[str, Any] = json.loads(content)
            return (
                str(data.get("summary", ""))[:500],
                bool(data.get("is_game_related", False)),
                bool(data.get("is_noise", False)),
            )
        except Exception:
            return self.fallback.analyze(keyword, trend)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GamePage:
    site_name: str
    url: str
    slug: str
    title: str
    discovered_at: datetime
    lastmod: str | None = None


@dataclass(frozen=True)
class KeywordCandidate:
    keyword: str
    canonical_keyword: str
    game_url: str
    site_name: str
    source: str


@dataclass(frozen=True)
class TrendResult:
    keyword: str
    canonical_keyword: str
    provider: str
    graph_values: list[int] = field(default_factory=list)
    related_top: list[str] = field(default_factory=list)
    related_rising: list[tuple[str, str]] = field(default_factory=list)
    raw: dict | None = None


@dataclass(frozen=True)
class ScoreResult:
    keyword: str
    score: int
    status: str
    reasons: list[str]
    intent_summary: str = ""
    is_noise: bool = False
    evidence_score: int = 0

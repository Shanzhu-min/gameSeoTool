from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SiteConfig:
    name: str
    sitemap_url: str
    url_patterns: list[str] = field(default_factory=list)
    enabled: bool = True
    weight: float = 1.0


@dataclass(frozen=True)
class Defaults:
    location_name: str = "United States"
    language_name: str = "English"
    trend_type: str = "web"
    date_range_days: int = 30
    max_keywords_per_game: int = 4
    push_score_threshold: int = 60
    observe_score_threshold: int = 40
    candidate_min_evidence_score: int = 35
    report_score_threshold: int = 60


@dataclass(frozen=True)
class AppConfig:
    database_path: Path
    defaults: Defaults
    sites: list[SiteConfig]


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    load_dotenv_file()
    config_path = Path(path or os.getenv("GAMESEO_CONFIG", "config/sites.example.json"))
    with config_path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    defaults = Defaults(**raw.get("defaults", {}))
    database_path = Path(os.getenv("GAMESEO_DB_PATH", raw.get("database_path", "data/gameseo.sqlite3")))
    sites = [SiteConfig(**item) for item in raw.get("sites", []) if item.get("enabled", True)]
    return AppConfig(database_path=database_path, defaults=defaults, sites=sites)


def load_dotenv_file(path: str | os.PathLike[str] = ".env") -> None:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

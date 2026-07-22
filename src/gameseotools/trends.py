from __future__ import annotations

import base64
from datetime import date, timedelta
import os
import time
from typing import Any, Protocol

from .config import Defaults, load_dotenv_file
from .http import get_json, post_json
from .models import TrendResult


class TrendProvider(Protocol):
    name: str

    def fetch(self, keyword: str) -> TrendResult:
        ...


class EmptyTrendProvider:
    name = "empty"

    def fetch(self, keyword: str) -> TrendResult:
        return TrendResult(keyword=keyword, canonical_keyword=keyword, provider=self.name)


class DataForSEOTrendProvider:
    name = "dataforseo"
    endpoint = "https://api.dataforseo.com/v3/keywords_data/google_trends/explore/live"
    user_data_endpoint = "https://api.dataforseo.com/v3/appendix/user_data"

    def __init__(self, login: str, password: str, defaults: Defaults, retries: int = 2, retry_delay: float = 2.0):
        self.login = login
        self.password = password
        self.defaults = defaults
        self.retries = retries
        self.retry_delay = retry_delay

    @classmethod
    def from_env(cls, defaults: Defaults) -> "DataForSEOTrendProvider | None":
        login = os.getenv("DATAFORSEO_LOGIN")
        password = os.getenv("DATAFORSEO_PASSWORD")
        if not login or not password:
            return None
        return cls(login=login, password=password, defaults=defaults)

    def fetch(self, keyword: str) -> TrendResult:
        today = date.today()
        payload = [
            {
                "location_name": self.defaults.location_name,
                "language_name": self.defaults.language_name,
                "date_from": (today - timedelta(days=self.defaults.date_range_days)).isoformat(),
                "date_to": today.isoformat(),
                "type": self.defaults.trend_type,
                "keywords": [keyword],
            }
        ]
        token = base64.b64encode(f"{self.login}:{self.password}".encode("utf-8")).decode("ascii")
        response = self.post_with_retry(payload, token)
        return parse_dataforseo_response(keyword, response)

    def post_with_retry(self, payload: list[dict[str, Any]], token: str) -> dict[str, Any]:
        last_error: RuntimeError | None = None
        for attempt in range(self.retries + 1):
            try:
                return post_json(self.endpoint, payload, headers={"Authorization": f"Basic {token}"}, timeout=90)
            except RuntimeError as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.retry_delay * (attempt + 1))
        raise last_error or RuntimeError("DataForSEO request failed")

    def check_account(self) -> dict[str, Any]:
        token = base64.b64encode(f"{self.login}:{self.password}".encode("utf-8")).decode("ascii")
        return get_json(self.user_data_endpoint, headers={"Authorization": f"Basic {token}"}, timeout=60)


def dataforseo_provider_from_env(defaults: Defaults) -> DataForSEOTrendProvider | None:
    load_dotenv_file()
    return DataForSEOTrendProvider.from_env(defaults)


def parse_dataforseo_response(keyword: str, raw: dict[str, Any]) -> TrendResult:
    graph_values: list[int] = []
    related_top: list[str] = []
    related_rising: list[tuple[str, str]] = []

    tasks = raw.get("tasks") or []
    for task in tasks:
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                item_type = item.get("type")
                data = item.get("data")
                if item_type == "google_trends_graph" and isinstance(data, list):
                    for point in data:
                        values = point.get("values") or []
                        if values:
                            try:
                                graph_values.append(int(values[0]))
                            except (TypeError, ValueError):
                                continue
                elif item_type == "google_trends_queries_list" and isinstance(data, dict):
                    for row in data.get("top") or []:
                        query = row.get("query")
                        if query:
                            related_top.append(str(query))
                    for row in data.get("rising") or []:
                        query = row.get("query")
                        value = row.get("value", "")
                        if query:
                            related_rising.append((str(query), str(value)))

    return TrendResult(
        keyword=keyword,
        canonical_keyword=keyword,
        provider="dataforseo",
        graph_values=graph_values,
        related_top=related_top,
        related_rising=related_rising,
        raw=raw,
    )

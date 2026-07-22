from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import GamePage, KeywordCandidate, ScoreResult, TrendResult


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                lastmod TEXT,
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                game_url TEXT NOT NULL,
                site_name TEXT NOT NULL,
                source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                pushed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS trend_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                provider TEXT NOT NULL,
                score INTEGER NOT NULL,
                status TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                intent_summary TEXT,
                is_noise INTEGER NOT NULL DEFAULT 0,
                graph_values_json TEXT NOT NULL,
                related_top_json TEXT NOT NULL,
                related_rising_json TEXT NOT NULL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                channel TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                success INTEGER NOT NULL,
                response_text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                params_json TEXT NOT NULL,
                output_text TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            """
        )
        self.conn.commit()

    def upsert_pages(self, pages: Iterable[GamePage]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        now = utc_now()
        for page in pages:
            cursor = self.conn.execute("SELECT id FROM game_pages WHERE url = ?", (page.url,))
            exists = cursor.fetchone() is not None
            if exists:
                self.conn.execute(
                    """
                    UPDATE game_pages
                    SET site_name = ?, slug = ?, title = ?, lastmod = ?, updated_at = ?
                    WHERE url = ?
                    """,
                    (page.site_name, page.slug, page.title, page.lastmod, now, page.url),
                )
                updated += 1
            else:
                self.conn.execute(
                    """
                    INSERT INTO game_pages (site_name, url, slug, title, lastmod, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (page.site_name, page.url, page.slug, page.title, page.lastmod, page.discovered_at.isoformat(), now),
                )
                inserted += 1
        self.conn.commit()
        return inserted, updated

    def upsert_keywords(self, candidates: Iterable[KeywordCandidate]) -> tuple[int, int]:
        inserted = 0
        seen = 0
        now = utc_now()
        for candidate in candidates:
            cursor = self.conn.execute("SELECT id FROM keywords WHERE keyword = ?", (candidate.keyword,))
            exists = cursor.fetchone() is not None
            if exists:
                self.conn.execute(
                    "UPDATE keywords SET last_seen_at = ? WHERE keyword = ?",
                    (now, candidate.keyword),
                )
                seen += 1
            else:
                self.conn.execute(
                    """
                    INSERT INTO keywords (keyword, game_url, site_name, source, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (candidate.keyword, candidate.game_url, candidate.site_name, candidate.source, now, now),
                )
                inserted += 1
        self.conn.commit()
        return inserted, seen

    def get_keywords_for_processing(self, limit: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT keyword, game_url, site_name, source, first_seen_at, pushed_at
                FROM keywords
                ORDER BY datetime(last_seen_at) DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def keyword_was_pushed(self, keyword: str) -> bool:
        cursor = self.conn.execute("SELECT pushed_at FROM keywords WHERE keyword = ?", (keyword,))
        row = cursor.fetchone()
        return bool(row and row["pushed_at"])

    def save_trend_result(self, trend: TrendResult, score: ScoreResult) -> None:
        self.conn.execute(
            """
            INSERT INTO trend_results (
                keyword, provider, score, status, reasons_json, intent_summary, is_noise,
                graph_values_json, related_top_json, related_rising_json, raw_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trend.keyword,
                trend.provider,
                score.score,
                score.status,
                json.dumps(score.reasons, ensure_ascii=False),
                score.intent_summary,
                1 if score.is_noise else 0,
                json.dumps(trend.graph_values, ensure_ascii=False),
                json.dumps(trend.related_top, ensure_ascii=False),
                json.dumps(trend.related_rising, ensure_ascii=False),
                json.dumps(trend.raw, ensure_ascii=False) if trend.raw else None,
                utc_now(),
            ),
        )
        self.conn.commit()

    def mark_pushed(self, keyword: str) -> None:
        self.conn.execute("UPDATE keywords SET pushed_at = ? WHERE keyword = ?", (utc_now(), keyword))
        self.conn.commit()

    def log_notification(
        self,
        keyword: str,
        channel: str,
        payload: dict,
        success: bool,
        response_text: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO notifications (keyword, channel, payload_json, success, response_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (keyword, channel, json.dumps(payload, ensure_ascii=False), 1 if success else 0, response_text, utc_now()),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        result = {}
        for table in ["game_pages", "keywords", "trend_results", "notifications", "task_runs"]:
            cursor = self.conn.execute(f"SELECT COUNT(*) AS count FROM {table}")
            result[table] = int(cursor.fetchone()["count"])
        return result

    def trend_status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM trend_results
            GROUP BY status
            """
        )
        return {row["status"]: int(row["count"]) for row in rows}

    def recent_trend_results(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT tr.keyword, tr.provider, tr.score, tr.status, tr.reasons_json,
                       tr.intent_summary, tr.is_noise, tr.graph_values_json,
                       tr.related_top_json, tr.related_rising_json, tr.created_at,
                       k.site_name, k.game_url, k.source
                FROM trend_results tr
                LEFT JOIN keywords k ON k.keyword = tr.keyword
                ORDER BY datetime(tr.created_at) DESC, tr.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def trend_results(
        self,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[sqlite3.Row]:
        conditions = []
        params: list[str | int] = []
        if status:
            conditions.append("tr.status = ?")
            params.append(status)
        if query:
            conditions.append("tr.keyword LIKE ?")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        return list(
            self.conn.execute(
                f"""
                SELECT tr.id, tr.keyword, tr.provider, tr.score, tr.status, tr.reasons_json,
                       tr.intent_summary, tr.is_noise, tr.graph_values_json,
                       tr.related_top_json, tr.related_rising_json, tr.created_at,
                       k.site_name, k.game_url, k.source
                FROM trend_results tr
                LEFT JOIN keywords k ON k.keyword = tr.keyword
                {where}
                ORDER BY datetime(tr.created_at) DESC, tr.id DESC
                LIMIT ?
                """,
                params,
            )
        )

    def get_trend_result(self, result_id: int) -> sqlite3.Row | None:
        cursor = self.conn.execute(
            """
            SELECT tr.id, tr.keyword, tr.provider, tr.score, tr.status, tr.reasons_json,
                   tr.intent_summary, tr.is_noise, tr.graph_values_json,
                   tr.related_top_json, tr.related_rising_json, tr.raw_json, tr.created_at,
                   k.site_name, k.game_url, k.source
            FROM trend_results tr
            LEFT JOIN keywords k ON k.keyword = tr.keyword
            WHERE tr.id = ?
            """,
            (result_id,),
        )
        return cursor.fetchone()

    def create_task_run(self, task_type: str, params: dict) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO task_runs (task_type, status, params_json, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_type, "running", json.dumps(params, ensure_ascii=False), utc_now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_task_run(self, task_id: int, status: str, output_text: str) -> None:
        self.conn.execute(
            """
            UPDATE task_runs
            SET status = ?, output_text = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, output_text[-20000:], utc_now(), task_id),
        )
        self.conn.commit()

    def recent_task_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT id, task_type, status, params_json, output_text, started_at, finished_at
                FROM task_runs
                ORDER BY datetime(started_at) DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

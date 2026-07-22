from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .keywords import canonical_keyword
from .lifecycle import cooldown_until, evidence_score_for_candidate, lifecycle_after_score
from .models import GamePage, KeywordCandidate, ScoreResult, TrendResult


class DatabaseProtocol(Protocol):
    def close(self) -> None: ...
    def migrate(self) -> None: ...
    def upsert_pages(self, pages: Iterable[GamePage]) -> tuple[int, int]: ...
    def upsert_keywords(self, candidates: Iterable[KeywordCandidate]) -> tuple[int, int]: ...
    def get_keywords_for_processing(self, limit: int, min_evidence_score: int = 0): ...
    def keyword_was_pushed(self, keyword: str) -> bool: ...
    def save_trend_result(self, trend: TrendResult, score: ScoreResult) -> None: ...
    def update_keyword_lifecycle(self, keyword: str, trend: TrendResult, score: ScoreResult) -> None: ...
    def mark_pushed(self, keyword: str) -> None: ...
    def log_notification(self, keyword: str, channel: str, payload: dict, success: bool, response_text: str = "") -> None: ...
    def stats(self) -> dict[str, int]: ...
    def trend_status_counts(self) -> dict[str, int]: ...
    def lifecycle_counts(self) -> dict[str, int]: ...
    def recent_trend_results(self, limit: int = 50): ...
    def trend_results(self, limit: int = 100, status: str | None = None, query: str | None = None): ...
    def get_trend_result(self, result_id: int): ...
    def create_task_run(self, task_type: str, params: dict) -> int: ...
    def finish_task_run(self, task_id: int, status: str, output_text: str) -> None: ...
    def recent_task_runs(self, limit: int = 20): ...
    def mark_stale_task_runs(self, max_age_minutes: int = 30) -> int: ...


class Database:
    """Factory wrapper.

    Set SUPABASE_DB_URL, DATABASE_URL, or POSTGRES_URL to use Supabase/Postgres.
    Without those variables the app keeps using local SQLite for development.
    """

    def __new__(cls, path: Path) -> DatabaseProtocol:
        postgres_url = postgres_database_url()
        if postgres_url:
            return PostgresDatabase(postgres_url)
        return SQLiteDatabase(path)


class SQLiteDatabase:
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
                canonical_keyword TEXT,
                game_url TEXT NOT NULL,
                site_name TEXT NOT NULL,
                source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                pushed_at TEXT,
                lifecycle_status TEXT NOT NULL DEFAULT 'new_candidate',
                evidence_score INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT,
                review_count INTEGER NOT NULL DEFAULT 0,
                drop_count INTEGER NOT NULL DEFAULT 0,
                last_scored_at TEXT,
                last_recommended_at TEXT,
                lifecycle_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS trend_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                canonical_keyword TEXT,
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
                evidence_score INTEGER NOT NULL DEFAULT 0,
                opportunity_score INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.ensure_columns()
        self.backfill_canonical_keywords_once()
        self.conn.commit()

    def ensure_columns(self) -> None:
        for statement in [
            "ALTER TABLE keywords ADD COLUMN canonical_keyword TEXT",
            "ALTER TABLE trend_results ADD COLUMN canonical_keyword TEXT",
            "ALTER TABLE keywords ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'new_candidate'",
            "ALTER TABLE keywords ADD COLUMN evidence_score INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keywords ADD COLUMN cooldown_until TEXT",
            "ALTER TABLE keywords ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keywords ADD COLUMN drop_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE keywords ADD COLUMN last_scored_at TEXT",
            "ALTER TABLE keywords ADD COLUMN last_recommended_at TEXT",
            "ALTER TABLE keywords ADD COLUMN lifecycle_reason TEXT",
            "ALTER TABLE trend_results ADD COLUMN evidence_score INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE trend_results ADD COLUMN opportunity_score INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                self.conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        self.conn.execute("UPDATE keywords SET canonical_keyword = keyword WHERE canonical_keyword IS NULL OR canonical_keyword = ''")
        self.conn.execute("UPDATE trend_results SET canonical_keyword = keyword WHERE canonical_keyword IS NULL OR canonical_keyword = ''")
        self.conn.execute("UPDATE keywords SET lifecycle_status = 'new_candidate' WHERE lifecycle_status IS NULL OR lifecycle_status = ''")

    def backfill_canonical_keywords_once(self) -> None:
        cursor = self.conn.execute("SELECT value FROM schema_meta WHERE key = 'canonical_v3'")
        row = cursor.fetchone()
        if row and row["value"] == "done":
            return
        rows = self.conn.execute("SELECT keyword, site_name FROM keywords").fetchall()
        for row in rows:
            canonical = canonical_keyword(row["keyword"], site_name=row["site_name"])
            self.conn.execute(
                "UPDATE keywords SET canonical_keyword = ? WHERE keyword = ?",
                (canonical, row["keyword"]),
            )
        self.conn.execute(
            """
            UPDATE trend_results
            SET canonical_keyword = (
                SELECT canonical_keyword
                FROM keywords
                WHERE keywords.keyword = trend_results.keyword
            )
            WHERE EXISTS (
                SELECT 1
                FROM keywords
                WHERE keywords.keyword = trend_results.keyword
            )
            """
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('canonical_v3', 'done')"
        )

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
                    "UPDATE keywords SET canonical_keyword = ?, last_seen_at = ? WHERE keyword = ?",
                    (candidate.canonical_keyword, now, candidate.keyword),
                )
                seen += 1
            else:
                self.conn.execute(
                    """
                    INSERT INTO keywords (
                        keyword, canonical_keyword, game_url, site_name, source,
                        first_seen_at, last_seen_at, lifecycle_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.keyword,
                        candidate.canonical_keyword,
                        candidate.game_url,
                        candidate.site_name,
                        candidate.source,
                        now,
                        now,
                        "new_candidate",
                    ),
                )
                inserted += 1
        self.conn.commit()
        return inserted, seen

    def get_keywords_for_processing(self, limit: int, min_evidence_score: int = 0) -> list[dict]:
        rows = list(
            self.conn.execute(
                """
                SELECT canonical_keyword AS keyword,
                       canonical_keyword,
                       MIN(game_url) AS game_url,
                       MIN(site_name) AS site_name,
                       MIN(source) AS source,
                       MIN(first_seen_at) AS first_seen_at,
                       MAX(last_seen_at) AS last_seen_at,
                       MAX(pushed_at) AS pushed_at,
                       MIN(COALESCE(lifecycle_status, 'new_candidate')) AS lifecycle_status,
                       MAX(evidence_score) AS evidence_score,
                       MAX(cooldown_until) AS cooldown_until,
                       MAX(review_count) AS review_count,
                       MAX(drop_count) AS drop_count,
                       COUNT(*) AS variant_count,
                       COUNT(DISTINCT site_name) AS source_count
                FROM keywords
                WHERE COALESCE(lifecycle_status, 'new_candidate') NOT IN ('old_game', 'noise', 'archived')
                  AND (cooldown_until IS NULL OR datetime(cooldown_until) <= datetime('now'))
                GROUP BY canonical_keyword
                ORDER BY datetime(MAX(last_seen_at)) DESC
                LIMIT ?
                """,
                (max(limit * 5, limit),),
            )
        )
        ranked: list[dict] = []
        for row in rows:
            item = dict(row)
            evidence_score, reasons = evidence_score_for_candidate(item)
            item["evidence_score"] = evidence_score
            item["evidence_reasons"] = "; ".join(reasons)
            self.conn.execute(
                """
                UPDATE keywords
                SET evidence_score = ?
                WHERE canonical_keyword = ?
                """,
                (evidence_score, item["canonical_keyword"]),
            )
            if evidence_score >= min_evidence_score:
                ranked.append(item)
        self.conn.commit()
        ranked.sort(key=lambda item: (item["evidence_score"], item["last_seen_at"]), reverse=True)
        return ranked[:limit]

    def keyword_was_pushed(self, keyword: str) -> bool:
        cursor = self.conn.execute("SELECT pushed_at FROM keywords WHERE keyword = ?", (keyword,))
        row = cursor.fetchone()
        return bool(row and row["pushed_at"])

    def save_trend_result(self, trend: TrendResult, score: ScoreResult) -> None:
        self.conn.execute(
            """
            INSERT INTO trend_results (
                keyword, canonical_keyword, provider, score, status, reasons_json, intent_summary, is_noise,
                graph_values_json, related_top_json, related_rising_json, raw_json,
                evidence_score, opportunity_score, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            trend_result_values(trend, score),
        )
        self.conn.commit()

    def update_keyword_lifecycle(self, keyword: str, trend: TrendResult, score: ScoreResult) -> None:
        lifecycle_status, cooldown_days, reason = lifecycle_after_score(score, trend.graph_values)
        now = utc_now()
        recommended_at = now if lifecycle_status == "recommended" else None
        self.conn.execute(
            """
            UPDATE keywords
            SET lifecycle_status = ?,
                cooldown_until = ?,
                last_scored_at = ?,
                last_recommended_at = COALESCE(?, last_recommended_at),
                lifecycle_reason = ?,
                evidence_score = ?,
                review_count = COALESCE(review_count, 0) + 1,
                drop_count = COALESCE(drop_count, 0) + CASE WHEN ? = 'drop' THEN 1 ELSE 0 END
            WHERE canonical_keyword = ? OR keyword = ?
            """,
            (
                lifecycle_status,
                cooldown_until(cooldown_days),
                now,
                recommended_at,
                reason,
                score.evidence_score,
                score.status,
                trend.canonical_keyword or keyword,
                keyword,
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
        for table in TABLES:
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

    def lifecycle_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(lifecycle_status, 'new_candidate') AS status, COUNT(*) AS count
            FROM keywords
            GROUP BY COALESCE(lifecycle_status, 'new_candidate')
            """
        )
        return {row["status"]: int(row["count"]) for row in rows}

    def recent_trend_results(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.trend_results(limit=limit)

    def trend_results(
        self,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[sqlite3.Row]:
        conditions = ["tr.id IN (SELECT MAX(id) FROM trend_results GROUP BY COALESCE(NULLIF(canonical_keyword, ''), keyword))"]
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
                       tr.canonical_keyword, tr.intent_summary, tr.is_noise, tr.graph_values_json,
                       tr.related_top_json, tr.related_rising_json, tr.created_at,
                       tr.evidence_score, tr.opportunity_score,
                       k.site_name, k.game_url, k.source, k.lifecycle_status, k.cooldown_until,
                       k.review_count, k.drop_count, k.lifecycle_reason, k.first_seen_at, k.last_seen_at,
                       (SELECT COUNT(*) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variant_count,
                       (SELECT GROUP_CONCAT(keyword, '; ') FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variants
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
                   tr.canonical_keyword, tr.intent_summary, tr.is_noise, tr.graph_values_json,
                   tr.related_top_json, tr.related_rising_json, tr.raw_json, tr.created_at,
                   tr.evidence_score, tr.opportunity_score,
                   k.site_name, k.game_url, k.source, k.lifecycle_status, k.cooldown_until,
                   k.review_count, k.drop_count, k.lifecycle_reason, k.first_seen_at, k.last_seen_at,
                   (SELECT COUNT(*) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variant_count,
                   (SELECT GROUP_CONCAT(keyword, '; ') FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variants
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

    def mark_stale_task_runs(self, max_age_minutes: int = 30) -> int:
        cursor = self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'failed',
                output_text = COALESCE(output_text, '') || ?,
                finished_at = ?
            WHERE status = 'running'
              AND datetime(started_at) < datetime('now', ?)
            """,
            (
                "\n[stale] Task exceeded the runtime window and was marked failed.",
                utc_now(),
                f"-{max_age_minutes} minutes",
            ),
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def backend_name(self) -> str:
        return f"sqlite:{self.path}"


class PostgresDatabase:
    def __init__(self, database_url: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "Postgres persistence requires psycopg. Install dependencies from pyproject.toml."
            ) from exc

        self.psycopg = psycopg
        self.database_url = normalize_postgres_url(database_url)
        self.conn = psycopg.connect(self.database_url, row_factory=dict_row)

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS game_pages (
                    id BIGSERIAL PRIMARY KEY,
                    site_name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    lastmod TEXT,
                    discovered_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS keywords (
                    id BIGSERIAL PRIMARY KEY,
                    keyword TEXT NOT NULL UNIQUE,
                    canonical_keyword TEXT,
                    game_url TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    pushed_at TIMESTAMPTZ,
                    lifecycle_status TEXT NOT NULL DEFAULT 'new_candidate',
                    evidence_score INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TIMESTAMPTZ,
                    review_count INTEGER NOT NULL DEFAULT 0,
                    drop_count INTEGER NOT NULL DEFAULT 0,
                    last_scored_at TIMESTAMPTZ,
                    last_recommended_at TIMESTAMPTZ,
                    lifecycle_reason TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS trend_results (
                    id BIGSERIAL PRIMARY KEY,
                    keyword TEXT NOT NULL,
                    canonical_keyword TEXT,
                    provider TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    intent_summary TEXT,
                    is_noise BOOLEAN NOT NULL DEFAULT FALSE,
                    graph_values_json TEXT NOT NULL,
                    related_top_json TEXT NOT NULL,
                    related_rising_json TEXT NOT NULL,
                    raw_json TEXT,
                    evidence_score INTEGER NOT NULL DEFAULT 0,
                    opportunity_score INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id BIGSERIAL PRIMARY KEY,
                    keyword TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    response_text TEXT,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id BIGSERIAL PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    output_text TEXT,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS canonical_keyword TEXT")
            cur.execute("ALTER TABLE trend_results ADD COLUMN IF NOT EXISTS canonical_keyword TEXT")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'new_candidate'")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS evidence_score INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS cooldown_until TIMESTAMPTZ")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS review_count INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS drop_count INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS last_scored_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS last_recommended_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE keywords ADD COLUMN IF NOT EXISTS lifecycle_reason TEXT")
            cur.execute("ALTER TABLE trend_results ADD COLUMN IF NOT EXISTS evidence_score INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE trend_results ADD COLUMN IF NOT EXISTS opportunity_score INTEGER NOT NULL DEFAULT 0")
            cur.execute("UPDATE keywords SET canonical_keyword = keyword WHERE canonical_keyword IS NULL OR canonical_keyword = ''")
            cur.execute("UPDATE trend_results SET canonical_keyword = keyword WHERE canonical_keyword IS NULL OR canonical_keyword = ''")
            cur.execute("UPDATE keywords SET lifecycle_status = 'new_candidate' WHERE lifecycle_status IS NULL OR lifecycle_status = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_keywords_last_seen_at ON keywords (last_seen_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_keywords_canonical_keyword ON keywords (canonical_keyword)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_keywords_lifecycle_status ON keywords (lifecycle_status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_keywords_cooldown_until ON keywords (cooldown_until)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trend_results_created_at ON trend_results (created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trend_results_status ON trend_results (status)")
        self.conn.commit()
        self.backfill_canonical_keywords_once()

    def upsert_pages(self, pages: Iterable[GamePage]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        now = utc_now()
        with self.conn.cursor() as cur:
            for page in pages:
                cur.execute("SELECT id FROM game_pages WHERE url = %s", (page.url,))
                exists = cur.fetchone() is not None
                cur.execute(
                    """
                    INSERT INTO game_pages (site_name, url, slug, title, lastmod, discovered_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (url) DO UPDATE SET
                        site_name = EXCLUDED.site_name,
                        slug = EXCLUDED.slug,
                        title = EXCLUDED.title,
                        lastmod = EXCLUDED.lastmod,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (page.site_name, page.url, page.slug, page.title, page.lastmod, page.discovered_at.isoformat(), now),
                )
                if exists:
                    updated += 1
                else:
                    inserted += 1
        self.conn.commit()
        return inserted, updated

    def backfill_canonical_keywords_once(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM schema_meta WHERE key = %s", ("canonical_v3",))
            row = cur.fetchone()
            if row and row["value"] == "done":
                return
            cur.execute("SELECT keyword, site_name FROM keywords")
            rows = cur.fetchall()
            for row in rows:
                canonical = canonical_keyword(row["keyword"], site_name=row["site_name"])
                cur.execute(
                    "UPDATE keywords SET canonical_keyword = %s WHERE keyword = %s",
                    (canonical, row["keyword"]),
                )
            cur.execute(
                """
                UPDATE trend_results
                SET canonical_keyword = keywords.canonical_keyword
                FROM keywords
                WHERE keywords.keyword = trend_results.keyword
                """
            )
            cur.execute(
                """
                INSERT INTO schema_meta (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                ("canonical_v3", "done"),
            )
        self.conn.commit()

    def upsert_keywords(self, candidates: Iterable[KeywordCandidate]) -> tuple[int, int]:
        inserted = 0
        seen = 0
        now = utc_now()
        with self.conn.cursor() as cur:
            for candidate in candidates:
                cur.execute("SELECT id FROM keywords WHERE keyword = %s", (candidate.keyword,))
                exists = cur.fetchone() is not None
                cur.execute(
                    """
                    INSERT INTO keywords (
                        keyword, canonical_keyword, game_url, site_name, source,
                        first_seen_at, last_seen_at, lifecycle_status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (keyword) DO UPDATE SET
                        canonical_keyword = EXCLUDED.canonical_keyword,
                        last_seen_at = EXCLUDED.last_seen_at
                    """,
                    (
                        candidate.keyword,
                        candidate.canonical_keyword,
                        candidate.game_url,
                        candidate.site_name,
                        candidate.source,
                        now,
                        now,
                        "new_candidate",
                    ),
                )
                if exists:
                    seen += 1
                else:
                    inserted += 1
        self.conn.commit()
        return inserted, seen

    def get_keywords_for_processing(self, limit: int, min_evidence_score: int = 0):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT canonical_keyword AS keyword,
                       canonical_keyword,
                       MIN(game_url) AS game_url,
                       MIN(site_name) AS site_name,
                       MIN(source) AS source,
                       MIN(first_seen_at) AS first_seen_at,
                       MAX(last_seen_at) AS last_seen_at,
                       MAX(pushed_at) AS pushed_at,
                       MIN(COALESCE(lifecycle_status, 'new_candidate')) AS lifecycle_status,
                       MAX(evidence_score) AS evidence_score,
                       MAX(cooldown_until) AS cooldown_until,
                       MAX(review_count) AS review_count,
                       MAX(drop_count) AS drop_count,
                       COUNT(*) AS variant_count,
                       COUNT(DISTINCT site_name) AS source_count
                FROM keywords
                WHERE COALESCE(lifecycle_status, 'new_candidate') NOT IN ('old_game', 'noise', 'archived')
                  AND (cooldown_until IS NULL OR cooldown_until <= NOW())
                GROUP BY canonical_keyword
                ORDER BY MAX(last_seen_at) DESC
                LIMIT %s
                """,
                (max(limit * 5, limit),),
            )
            rows = cur.fetchall()
            ranked: list[dict] = []
            for row in rows:
                item = dict(row)
                evidence_score, reasons = evidence_score_for_candidate(item)
                item["evidence_score"] = evidence_score
                item["evidence_reasons"] = "; ".join(reasons)
                cur.execute(
                    "UPDATE keywords SET evidence_score = %s WHERE canonical_keyword = %s",
                    (evidence_score, item["canonical_keyword"]),
                )
                if evidence_score >= min_evidence_score:
                    ranked.append(item)
        self.conn.commit()
        ranked.sort(key=lambda item: (item["evidence_score"], str(item["last_seen_at"])), reverse=True)
        return ranked[:limit]

    def keyword_was_pushed(self, keyword: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pushed_at FROM keywords WHERE keyword = %s", (keyword,))
            row = cur.fetchone()
        return bool(row and row["pushed_at"])

    def save_trend_result(self, trend: TrendResult, score: ScoreResult) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trend_results (
                    keyword, canonical_keyword, provider, score, status, reasons_json, intent_summary, is_noise,
                    graph_values_json, related_top_json, related_rising_json, raw_json,
                    evidence_score, opportunity_score, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                trend_result_values(trend, score),
            )
        self.conn.commit()

    def update_keyword_lifecycle(self, keyword: str, trend: TrendResult, score: ScoreResult) -> None:
        lifecycle_status, cooldown_days, reason = lifecycle_after_score(score, trend.graph_values)
        now = utc_now()
        recommended_at = now if lifecycle_status == "recommended" else None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE keywords
                SET lifecycle_status = %s,
                    cooldown_until = %s,
                    last_scored_at = %s,
                    last_recommended_at = COALESCE(%s, last_recommended_at),
                    lifecycle_reason = %s,
                    evidence_score = %s,
                    review_count = COALESCE(review_count, 0) + 1,
                    drop_count = COALESCE(drop_count, 0) + CASE WHEN %s = 'drop' THEN 1 ELSE 0 END
                WHERE canonical_keyword = %s OR keyword = %s
                """,
                (
                    lifecycle_status,
                    cooldown_until(cooldown_days),
                    now,
                    recommended_at,
                    reason,
                    score.evidence_score,
                    score.status,
                    trend.canonical_keyword or keyword,
                    keyword,
                ),
            )
        self.conn.commit()

    def mark_pushed(self, keyword: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE keywords SET pushed_at = %s WHERE keyword = %s", (utc_now(), keyword))
        self.conn.commit()

    def log_notification(
        self,
        keyword: str,
        channel: str,
        payload: dict,
        success: bool,
        response_text: str = "",
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO notifications (keyword, channel, payload_json, success, response_text, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (keyword, channel, json.dumps(payload, ensure_ascii=False), bool(success), response_text, utc_now()),
            )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        result = {}
        with self.conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"SELECT COUNT(*) AS count FROM {table}")
                result[table] = int(cur.fetchone()["count"])
        return result

    def trend_status_counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM trend_results
                GROUP BY status
                """
            )
            return {row["status"]: int(row["count"]) for row in cur.fetchall()}

    def lifecycle_counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(lifecycle_status, 'new_candidate') AS status, COUNT(*) AS count
                FROM keywords
                GROUP BY COALESCE(lifecycle_status, 'new_candidate')
                """
            )
            return {row["status"]: int(row["count"]) for row in cur.fetchall()}

    def recent_trend_results(self, limit: int = 50):
        return self.trend_results(limit=limit)

    def trend_results(
        self,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ):
        conditions = ["tr.id IN (SELECT MAX(id) FROM trend_results GROUP BY COALESCE(NULLIF(canonical_keyword, ''), keyword))"]
        params: list[str | int] = []
        if status:
            conditions.append("tr.status = %s")
            params.append(status)
        if query:
            conditions.append("tr.keyword ILIKE %s")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT tr.id, tr.keyword, tr.provider, tr.score, tr.status, tr.reasons_json,
                       tr.canonical_keyword, tr.intent_summary, tr.is_noise, tr.graph_values_json,
                       tr.related_top_json, tr.related_rising_json, tr.created_at,
                       tr.evidence_score, tr.opportunity_score,
                       k.site_name, k.game_url, k.source, k.lifecycle_status, k.cooldown_until,
                       k.review_count, k.drop_count, k.lifecycle_reason, k.first_seen_at, k.last_seen_at,
                       (SELECT COUNT(*) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variant_count,
                       (SELECT STRING_AGG(keyword, '; ' ORDER BY keyword) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variants
                FROM trend_results tr
                LEFT JOIN keywords k ON k.keyword = tr.keyword
                {where}
                ORDER BY tr.created_at DESC, tr.id DESC
                LIMIT %s
                """,
                params,
            )
            return cur.fetchall()

    def get_trend_result(self, result_id: int):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT tr.id, tr.keyword, tr.provider, tr.score, tr.status, tr.reasons_json,
                   tr.canonical_keyword, tr.intent_summary, tr.is_noise, tr.graph_values_json,
                   tr.related_top_json, tr.related_rising_json, tr.raw_json, tr.created_at,
                   tr.evidence_score, tr.opportunity_score,
                   k.site_name, k.game_url, k.source, k.lifecycle_status, k.cooldown_until,
                   k.review_count, k.drop_count, k.lifecycle_reason, k.first_seen_at, k.last_seen_at,
                   (SELECT COUNT(*) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variant_count,
                   (SELECT STRING_AGG(keyword, '; ' ORDER BY keyword) FROM keywords kv WHERE kv.canonical_keyword = tr.canonical_keyword) AS variants
                FROM trend_results tr
                LEFT JOIN keywords k ON k.keyword = tr.keyword
                WHERE tr.id = %s
                """,
                (result_id,),
            )
            return cur.fetchone()

    def create_task_run(self, task_type: str, params: dict) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO task_runs (task_type, status, params_json, started_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (task_type, "running", json.dumps(params, ensure_ascii=False), utc_now()),
            )
            task_id = int(cur.fetchone()["id"])
        self.conn.commit()
        return task_id

    def finish_task_run(self, task_id: int, status: str, output_text: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_runs
                SET status = %s, output_text = %s, finished_at = %s
                WHERE id = %s
                """,
                (status, output_text[-20000:], utc_now(), task_id),
            )
        self.conn.commit()

    def recent_task_runs(self, limit: int = 20):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, task_type, status, params_json, output_text, started_at, finished_at
                FROM task_runs
                ORDER BY started_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()

    def mark_stale_task_runs(self, max_age_minutes: int = 30) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_runs
                SET status = 'failed',
                    output_text = COALESCE(output_text, '') || %s,
                    finished_at = %s
                WHERE status = 'running'
                  AND started_at < NOW() - (%s * INTERVAL '1 minute')
                """,
                (
                    "\n[stale] Task exceeded the runtime window and was marked failed.",
                    utc_now(),
                    max_age_minutes,
                ),
            )
            count = cur.rowcount
        self.conn.commit()
        return int(count)

    def backend_name(self) -> str:
        return "postgres"


TABLES = ["game_pages", "keywords", "trend_results", "notifications", "task_runs"]


def postgres_database_url() -> str:
    return (
        os.getenv("SUPABASE_DB_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or ""
    )


def normalize_postgres_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if "sslmode=" not in url:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}sslmode=require"
    return url


def trend_result_values(trend: TrendResult, score: ScoreResult) -> tuple:
    return (
        trend.keyword,
        trend.canonical_keyword or canonical_keyword(trend.keyword),
        trend.provider,
        score.score,
        score.status,
        json.dumps(score.reasons, ensure_ascii=False),
        score.intent_summary,
        bool(score.is_noise),
        json.dumps(trend.graph_values, ensure_ascii=False),
        json.dumps(trend.related_top, ensure_ascii=False),
        json.dumps(trend.related_rising, ensure_ascii=False),
        json.dumps(trend.raw, ensure_ascii=False) if trend.raw else None,
        score.evidence_score,
        score.score,
        utc_now(),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

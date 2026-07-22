import unittest
from pathlib import Path
import tempfile

from gameseotools.db import Database, normalize_postgres_url
from gameseotools.models import KeywordCandidate, ScoreResult, TrendResult


class DatabaseUrlTests(unittest.TestCase):
    def test_normalize_postgres_url_adds_sslmode(self):
        url = normalize_postgres_url("postgresql://user:pass@example.supabase.co:5432/postgres")
        self.assertTrue(url.endswith("?sslmode=require"))

    def test_normalize_postgres_url_converts_postgres_scheme(self):
        url = normalize_postgres_url("postgres://user:pass@example.supabase.co:5432/postgres?pool=true")
        self.assertTrue(url.startswith("postgresql://"))
        self.assertTrue(url.endswith("&sslmode=require"))


class KeywordLifecycleDatabaseTests(unittest.TestCase):
    def test_old_game_is_excluded_from_processing(self):
        path = Path(tempfile.gettempdir()) / "gameseo_lifecycle_test.sqlite3"
        if path.exists():
            path.unlink()
        db = Database(path)
        db.migrate()
        db.upsert_keywords(
            [
                KeywordCandidate(
                    keyword="connect 4",
                    canonical_keyword="connect 4",
                    game_url="https://example.com/connect-4",
                    site_name="y8",
                    source="test",
                )
            ]
        )
        trend = TrendResult(
            keyword="connect 4",
            canonical_keyword="connect 4",
            provider="test",
            graph_values=[90, 100, 80, 50, 30, 22, 21],
        )
        score = ScoreResult(keyword="connect 4", score=40, status="observe", reasons=[])
        db.update_keyword_lifecycle("connect 4", trend, score)
        rows = db.get_keywords_for_processing(10, min_evidence_score=0)
        db.close()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

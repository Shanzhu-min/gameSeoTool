import unittest

from gameseotools.db import normalize_postgres_url


class DatabaseUrlTests(unittest.TestCase):
    def test_normalize_postgres_url_adds_sslmode(self):
        url = normalize_postgres_url("postgresql://user:pass@example.supabase.co:5432/postgres")
        self.assertTrue(url.endswith("?sslmode=require"))

    def test_normalize_postgres_url_converts_postgres_scheme(self):
        url = normalize_postgres_url("postgres://user:pass@example.supabase.co:5432/postgres?pool=true")
        self.assertTrue(url.startswith("postgresql://"))
        self.assertTrue(url.endswith("&sslmode=require"))


if __name__ == "__main__":
    unittest.main()

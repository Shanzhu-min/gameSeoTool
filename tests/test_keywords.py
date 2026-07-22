from datetime import datetime, timezone
import unittest

from gameseotools.keywords import generate_keywords, normalize_keyword
from gameseotools.keywords import canonical_keyword
from gameseotools.models import GamePage


class KeywordTests(unittest.TestCase):
    def test_normalize_keyword_keeps_io_signal(self):
        self.assertEqual(normalize_keyword("BloxdHop-io"), "bloxdhop io")

    def test_canonical_keyword_strips_common_modifiers(self):
        self.assertEqual(canonical_keyword("connect 4 game"), "connect 4")
        self.assertEqual(canonical_keyword("connect 4 online"), "connect 4")
        self.assertEqual(canonical_keyword("connect 4 y8"), "connect 4")

    def test_generate_keywords_from_game_page(self):
        page = GamePage(
            site_name="poki",
            url="https://poki.com/en/g/going-up-rooftop",
            slug="going-up-rooftop",
            title="going up rooftop",
            discovered_at=datetime.now(timezone.utc),
        )
        keywords = generate_keywords(page, max_keywords=4)
        self.assertEqual(
            [item.keyword for item in keywords],
            [
                "going up rooftop",
                "going up rooftop game",
                "going up rooftop online",
                "going up rooftop poki",
            ],
        )
        self.assertEqual({item.canonical_keyword for item in keywords}, {"going up rooftop"})


if __name__ == "__main__":
    unittest.main()

import unittest

from gameseotools.models import TrendResult
from gameseotools.scoring import score_keyword


class ScoringTests(unittest.TestCase):
    def test_score_pushes_game_keyword_with_rising_query(self):
        trend = TrendResult(
            keyword="test game",
            canonical_keyword="test",
            provider="test",
            graph_values=[1, 2, 2, 5, 8, 30, 55],
            related_rising=[("test game unblocked", "500")],
        )
        score = score_keyword(
            keyword="test game",
            trend=trend,
            intent_summary="game related",
            is_game_related=True,
            is_noise=False,
            was_pushed=False,
            push_threshold=60,
            observe_threshold=40,
        )
        self.assertEqual(score.status, "push")
        self.assertGreaterEqual(score.score, 60)


if __name__ == "__main__":
    unittest.main()

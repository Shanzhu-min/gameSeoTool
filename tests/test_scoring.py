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

    def test_no_trend_data_drops_keyword(self):
        trend = TrendResult(
            keyword="kitten canons game",
            canonical_keyword="kitten canons",
            provider="test",
            graph_values=[],
            related_rising=[("kitten canons online", "100")],
        )
        score = score_keyword(
            keyword="kitten canons game",
            trend=trend,
            intent_summary="game related",
            is_game_related=True,
            is_noise=False,
            was_pushed=False,
            push_threshold=60,
            observe_threshold=40,
        )
        self.assertEqual(score.status, "drop")
        self.assertLess(score.score, 40)

    def test_declining_old_trend_cannot_push(self):
        trend = TrendResult(
            keyword="connect 4 game",
            canonical_keyword="connect 4",
            provider="test",
            graph_values=[80, 90, 100, 75, 42, 35, 30, 25, 23, 22, 21],
            related_rising=[("connect 4 online", "100")],
        )
        score = score_keyword(
            keyword="connect 4 game",
            trend=trend,
            intent_summary="game related",
            is_game_related=True,
            is_noise=False,
            was_pushed=False,
            push_threshold=60,
            observe_threshold=40,
        )
        self.assertEqual(score.status, "observe")
        self.assertLess(score.score, 60)

    def test_evidence_cannot_push_old_declining_trend(self):
        trend = TrendResult(
            keyword="connect 4 game",
            canonical_keyword="connect 4",
            provider="test",
            graph_values=[80, 90, 100, 75, 42, 35, 30, 25, 23, 22, 21],
            related_rising=[("connect 4 online", "100")],
        )
        score = score_keyword(
            keyword="connect 4 game",
            trend=trend,
            intent_summary="game related",
            is_game_related=True,
            is_noise=False,
            was_pushed=False,
            push_threshold=60,
            observe_threshold=40,
            evidence_score=90,
        )
        self.assertNotEqual(score.status, "push")
        self.assertLess(score.score, 60)


if __name__ == "__main__":
    unittest.main()

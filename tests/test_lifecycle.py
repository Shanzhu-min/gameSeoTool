import unittest
from datetime import datetime, timedelta, timezone

from gameseotools.lifecycle import evidence_score_for_candidate, lifecycle_after_score
from gameseotools.models import ScoreResult


class LifecycleTests(unittest.TestCase):
    def test_fresh_multi_variant_candidate_gets_evidence_score(self):
        row = {
            "keyword": "soul land awakening world",
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "site_name": "poki",
            "variant_count": 3,
            "source_count": 1,
            "drop_count": 0,
            "lifecycle_status": "new_candidate",
        }
        score, reasons = evidence_score_for_candidate(row)
        self.assertGreaterEqual(score, 45)
        self.assertTrue(any("First seen" in reason for reason in reasons))

    def test_inactive_lifecycle_is_excluded(self):
        score, reasons = evidence_score_for_candidate(
            {"keyword": "connect 4", "lifecycle_status": "old_game"}
        )
        self.assertEqual(score, 0)
        self.assertIn("excluded", reasons[0])

    def test_old_or_declining_sets_old_game(self):
        score = ScoreResult(
            keyword="connect 4",
            score=40,
            status="observe",
            reasons=[],
            evidence_score=55,
        )
        lifecycle_status, cooldown_days, reason = lifecycle_after_score(
            score,
            [80, 90, 100, 60, 40, 30, 25, 22, 21],
        )
        self.assertEqual(lifecycle_status, "old_game")
        self.assertEqual(cooldown_days, 30)
        self.assertIn("old_or_declining", reason)


if __name__ == "__main__":
    unittest.main()

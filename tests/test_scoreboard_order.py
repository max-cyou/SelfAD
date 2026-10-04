import unittest
from datetime import datetime, timedelta, timezone

from selfad.routes.participants import _scoreboard_sort_key


class ScoreboardOrderTests(unittest.TestCase):
    def test_equal_scores_use_time_current_score_was_reached(self):
        first = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
        rows = [
            {
                "username": "early-partial",
                "score": 100,
                "first_solution_at": first,
                "last_solution_at": first + timedelta(minutes=20),
            },
            {
                "username": "direct-full",
                "score": 100,
                "first_solution_at": first + timedelta(minutes=10),
                "last_solution_at": first + timedelta(minutes=10),
            },
            {
                "username": "same-finish-earlier-start",
                "score": 100,
                "first_solution_at": first,
                "last_solution_at": first + timedelta(minutes=20),
            },
        ]

        rows.sort(key=_scoreboard_sort_key)

        self.assertEqual(
            [row["username"] for row in rows],
            ["direct-full", "early-partial", "same-finish-earlier-start"],
        )


if __name__ == "__main__":
    unittest.main()

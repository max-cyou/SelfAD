import unittest
from datetime import datetime, timedelta, timezone

from selfad.routes.participants import _scoreboard_sort_key


class ScoreboardOrderTests(unittest.TestCase):
    def test_equal_scores_use_first_then_last_solution(self):
        first = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
        rows = [
            {
                "username": "later-last",
                "score": 100,
                "first_solution_at": first,
                "last_solution_at": first + timedelta(minutes=2),
            },
            {
                "username": "later-first",
                "score": 100,
                "first_solution_at": first + timedelta(seconds=1),
                "last_solution_at": first + timedelta(seconds=1),
            },
            {
                "username": "earlier-last",
                "score": 100,
                "first_solution_at": first,
                "last_solution_at": first + timedelta(minutes=1),
            },
        ]

        rows.sort(key=_scoreboard_sort_key)

        self.assertEqual(
            [row["username"] for row in rows],
            ["earlier-last", "later-last", "later-first"],
        )


if __name__ == "__main__":
    unittest.main()

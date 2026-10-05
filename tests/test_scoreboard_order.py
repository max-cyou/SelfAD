import unittest
from datetime import datetime, timedelta, timezone

from selfad.routes.participants import _assign_scoreboard_ranks, _scoreboard_sort_key


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

    def test_equal_scores_receive_unique_sequential_places(self):
        first = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
        rows = [
            {
                "username": "first",
                "score": 2000,
                "first_solution_at": first,
                "last_solution_at": first,
            },
            {
                "username": "second",
                "score": 2000,
                "first_solution_at": first + timedelta(minutes=1),
                "last_solution_at": first + timedelta(minutes=1),
            },
            {
                "username": "third",
                "score": 2000,
                "first_solution_at": first + timedelta(minutes=2),
                "last_solution_at": first + timedelta(minutes=2),
            },
            {
                "username": "fourth",
                "score": 1999,
                "first_solution_at": first,
                "last_solution_at": first,
            },
        ]

        rows.sort(key=_scoreboard_sort_key)
        _assign_scoreboard_ranks(rows)

        self.assertEqual([row["rank"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(
            [row["username"] for row in rows],
            ["first", "second", "third", "fourth"],
        )


if __name__ == "__main__":
    unittest.main()

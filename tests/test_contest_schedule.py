import unittest
from datetime import datetime, timedelta, timezone

from selfad.contest import ENDED, STARTED, contest_state, start_contest_if_due
from selfad.models import InstanceConfig


class ContestScheduleTests(unittest.TestCase):
    def test_automatic_stop_ends_and_persists_contest(self):
        now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
        config = InstanceConfig(
            id=1,
            contest_started=True,
            contest_ended=False,
            contest_ends_at=now - timedelta(seconds=1),
        )

        self.assertEqual(contest_state(config, now=now), ENDED)
        self.assertTrue(start_contest_if_due(config, now=now))
        self.assertTrue(config.contest_ended)

    def test_future_stop_does_not_interrupt_started_contest(self):
        now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
        config = InstanceConfig(
            id=1,
            contest_started=True,
            contest_ended=False,
            contest_ends_at=now + timedelta(minutes=5),
        )

        self.assertEqual(contest_state(config, now=now), STARTED)
        self.assertFalse(start_contest_if_due(config, now=now))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from selfad.rate_limit import SlidingWindowRateLimiter, client_key


class RateLimitTests(unittest.TestCase):
    def test_rejects_attempt_after_limit_then_recovers(self):
        limiter = SlidingWindowRateLimiter()
        with patch("selfad.rate_limit.time.monotonic", side_effect=[0, 1, 2, 61]):
            self.assertTrue(limiter.allow("login:127.0.0.1", limit=2, window_seconds=60))
            self.assertTrue(limiter.allow("login:127.0.0.1", limit=2, window_seconds=60))
            self.assertFalse(limiter.allow("login:127.0.0.1", limit=2, window_seconds=60))
            self.assertTrue(limiter.allow("login:127.0.0.1", limit=2, window_seconds=60))

    def test_unknown_client_has_stable_key(self):
        self.assertEqual(client_key(None, "registration"), "registration:unknown")


if __name__ == "__main__":
    unittest.main()

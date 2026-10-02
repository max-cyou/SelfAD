import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class PublicEventPreflightTests(unittest.TestCase):
    def test_preflight_requires_hsts_external_runner_and_metrics(self):
        script = (PROJECT_DIR / "scripts/event/event-preflight.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("strict-transport-security", script.lower())
        self.assertIn("SELFAD_PRECHECK_EXPECTED_RUNNER:-external", script)
        self.assertIn("SELFAD_METRICS_TOKEN is required", script)


if __name__ == "__main__":
    unittest.main()

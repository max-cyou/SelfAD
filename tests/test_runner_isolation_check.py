import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class RunnerIsolationCheckTests(unittest.TestCase):
    def test_check_exercises_internal_network_and_runtime_limits(self):
        script = (
            PROJECT_DIR / "scripts" / "event" / "runner-isolation-check.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("network create --internal", script)
        self.assertIn("--memory 256m", script)
        self.assertIn("--pids-limit 128", script)
        self.assertIn("--read-only", script)
        self.assertIn("1.1.1.1", script)
        self.assertIn("egress_blocked", script)


if __name__ == "__main__":
    unittest.main()

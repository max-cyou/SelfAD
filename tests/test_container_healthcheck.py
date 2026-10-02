import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class ContainerHealthcheckTests(unittest.TestCase):
    def test_healthcheck_is_a_control_plane_liveness_probe(self):
        script = (PROJECT_DIR / "docker" / "healthcheck.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("127.0.0.1:8929/api/healthz", script)
        self.assertIn("127.0.0.1:8000/health", script)
        self.assertNotIn("docker info", script)


if __name__ == "__main__":
    unittest.main()

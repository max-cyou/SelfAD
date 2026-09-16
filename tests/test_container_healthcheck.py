import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class ContainerHealthcheckTests(unittest.TestCase):
    def test_healthcheck_uses_configured_runner(self):
        script = (PROJECT_DIR / "docker" / "healthcheck.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("SELFAD_RUNNER_DOCKER_HOST", script)
        self.assertIn("SELFAD_RUNNER_TLS_VERIFY", script)
        self.assertIn("SELFAD_RUNNER_CERT_PATH", script)
        self.assertIn("docker info", script)


if __name__ == "__main__":
    unittest.main()

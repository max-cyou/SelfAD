import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class TemporaryRunnerComposeTests(unittest.TestCase):
    def test_runner_is_tls_only_and_not_host_published(self):
        compose = (PROJECT_DIR / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("docker:29-dind", compose)
        self.assertIn("DOCKER_TLS_CERTDIR: /certs", compose)
        self.assertIn("SELFAD_RUNNER_TLS_VERIFY: \"true\"", compose)
        self.assertIn("mem_limit: 768m", compose)
        self.assertIn("pids_limit: 256", compose)
        runner_section = compose.split("\n  selfad:", 1)[0]
        self.assertNotIn("ports:", runner_section)


if __name__ == "__main__":
    unittest.main()

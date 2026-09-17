import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RunnerInstallScriptTests(unittest.TestCase):
    def test_installer_requires_tls_firewall_and_fresh_host(self):
        script = (PROJECT_DIR / "scripts" / "runner-install.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("docker ps -aq", script)
        self.assertIn("SELFAD_RUNNER_FIREWALL_CONFIRMED", script)
        self.assertIn("--tlsverify", script)
        self.assertIn("--userns-remap=default", script)
        self.assertIn("--icc=false", script)


if __name__ == "__main__":
    unittest.main()

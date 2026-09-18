import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RunnerTlsScriptTests(unittest.TestCase):
    def test_tls_script_generates_separate_runner_and_client_bundles(self):
        script = (PROJECT_DIR / "scripts" / "event" / "runner-init-tls.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$output_dir/runner"', script)
        self.assertIn('"$output_dir/control-plane"', script)
        self.assertIn("extendedKeyUsage=serverAuth", script)
        self.assertIn("extendedKeyUsage=clientAuth", script)
        self.assertIn("subjectAltName=$subject_alt_name", script)


if __name__ == "__main__":
    unittest.main()

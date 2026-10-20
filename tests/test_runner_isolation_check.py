import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class RunnerIsolationCheckTests(unittest.TestCase):
    def test_check_exercises_internal_network_and_runtime_limits(self):
        script = (
            PROJECT_DIR / "scripts" / "event" / "runner-isolation-check.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("selfad.runtime.security_check", script)
        probe = (PROJECT_DIR / "selfad/runtime/security_check.py").read_text()
        for required in ('verify_builder()', 'check_api_authentication()', 'OOMKilled', 'PID_PROBE',
                         'gateway_mode_ipv4=isolated', '169.254.169.254', '2376', '1.1.1.1',
                         'no-new-privileges', 'socket.create_connection((sys.argv[1],8080)'):
            self.assertIn(required, probe)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_DIR / relative).read_text(encoding="utf-8")


class RunnerFailureReportingTests(unittest.TestCase):
    def test_stubborn_child_processes_become_runner_errors(self):
        runner = _read("selfad/runner.py")
        self.assertIn("def _terminate_process", runner)
        self.assertIn("except subprocess.TimeoutExpired", runner)
        self.assertIn("ignored the termination signal", runner)
        # The raw wait after the failure path must go through the wrapper.
        self.assertNotIn("process.wait(timeout=5)\n            raise RunnerError", runner)

    def test_script_failures_explain_themselves(self):
        runner = _read("selfad/runner.py")
        self.assertIn('_script_failure_message("Exploit script"', runner)
        self.assertIn('_script_failure_message("Jury injector"', runner)
        self.assertNotIn('"Jury exploit failed."', runner)

    def test_defense_failure_message_names_the_outcome(self):
        worker = _read("selfad/worker.py")
        self.assertIn(
            "Defense check failed: the jury exploit recovered",
            worker,
        )

    def test_webhook_caps_the_pending_queue_per_repository(self):
        webhooks = _read("selfad/routes/webhooks.py")
        self.assertIn("MAX_PENDING_EVENTS_PER_REPOSITORY = 25", webhooks)
        self.assertIn("previous pushes are still being checked", webhooks)


class SshKeyRotationTests(unittest.TestCase):
    def test_gitea_client_can_delete_a_previous_key(self):
        gitea = _read("selfad/gitea.py")
        self.assertIn("def delete_user_ssh_key", gitea)

    def test_admin_replace_flow_removes_the_old_key_first(self):
        admin = _read("selfad/routes/admin.py")
        self.assertNotIn("This user already has an SSH key.", admin)
        self.assertIn("delete_user_ssh_key", admin)
        self.assertIn('values["ssh_public_key"] != target.ssh_public_key', admin)


if __name__ == "__main__":
    unittest.main()

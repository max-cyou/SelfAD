import os
import unittest
from unittest.mock import patch

from selfad.runner import (
    CommandResult,
    RUNNER_LABEL,
    RUNNER_USER,
    _run_jury_script,
    _wait_for_healthcheck,
    cleanup_managed_runner_resources,
    runner_mode,
)
from selfad.settings import get_runner_settings


class RunnerSecurityTests(unittest.TestCase):
    def test_internal_runner_is_opt_in(self):
        with patch.dict(
            os.environ,
            {
                "SELFAD_ENABLE_INTERNAL_RUNNER": "false",
                "SELFAD_RUNNER_DOCKER_HOST": "unix:///run/selfad-docker/docker.sock",
            },
            clear=True,
        ):
            settings = get_runner_settings()
            self.assertFalse(settings.internal_runner_enabled)
            self.assertEqual(runner_mode(), "unavailable")

    def test_external_runner_settings_enable_tls(self):
        with patch.dict(
            os.environ,
            {
                "SELFAD_RUNNER_DOCKER_HOST": "tcp://runner.internal:2376",
                "SELFAD_RUNNER_TLS_VERIFY": "true",
                "SELFAD_RUNNER_CERT_PATH": "/run/runner-tls",
                "SELFAD_ENABLE_INTERNAL_RUNNER": "false",
            },
            clear=False,
        ):
            settings = get_runner_settings()
            self.assertEqual(settings.docker_host, "tcp://runner.internal:2376")
            self.assertTrue(settings.tls_verify)
            self.assertEqual(settings.cert_path, "/run/runner-tls")
            self.assertFalse(settings.internal_runner_enabled)
            self.assertEqual(runner_mode(), "external")

    @patch("selfad.runner._docker")
    def test_script_runner_is_non_root_and_restricted(self, docker):
        docker.return_value = CommandResult(0, "")
        _run_jury_script(
            container_name="selfad-test-script",
            network_name="selfad-test-network",
            script_name="exploit.py",
            target="http://target:8080",
            image="python:3.13-alpine",
        )
        arguments = docker.call_args.args[0]
        self.assertIn(RUNNER_LABEL, arguments)
        self.assertIn("--read-only", arguments)
        self.assertIn("no-new-privileges", arguments)
        self.assertIn("--cap-drop", arguments)
        self.assertIn("--pids-limit", arguments)
        self.assertIn("--user", arguments)
        self.assertEqual(arguments[arguments.index("--user") + 1], RUNNER_USER)
        self.assertIn("--network", arguments)
        self.assertNotIn("--volume", arguments)
        self.assertEqual(
            arguments[arguments.index("--network") + 1],
            "selfad-test-network",
        )

    @patch("selfad.runner._docker")
    def test_health_probe_runs_inside_the_isolated_network(self, docker):
        docker.return_value = CommandResult(0, "")
        _wait_for_healthcheck("selfad-test-network", 8080, "/health")
        arguments = docker.call_args.args[0]
        self.assertIn("--network", arguments)
        self.assertEqual(
            arguments[arguments.index("--network") + 1],
            "selfad-test-network",
        )
        self.assertIn("--read-only", arguments)
        self.assertIn("--user", arguments)
        self.assertEqual(arguments[arguments.index("--user") + 1], RUNNER_USER)
        self.assertIn("http://target:8080/health", arguments)

    @patch("selfad.runner._docker_quiet")
    @patch("selfad.runner._docker")
    def test_cleanup_only_targets_labeled_resources(self, docker, docker_quiet):
        docker.side_effect = [
            CommandResult(0, "25.0"),
            CommandResult(0, "container-id\n"),
            CommandResult(0, "network-id\n"),
            CommandResult(0, "image-id\n"),
        ]
        self.assertEqual(cleanup_managed_runner_resources(), 3)
        commands = [call.args[0] for call in docker_quiet.call_args_list]
        self.assertEqual(commands[0], ["rm", "--force", "container-id"])
        self.assertEqual(commands[1], ["network", "rm", "network-id"])
        self.assertEqual(commands[2], ["image", "rm", "--force", "image-id"])
        for command in [call.args[0] for call in docker.call_args_list[1:]]:
            self.assertIn(f"label={RUNNER_LABEL}", command)


if __name__ == "__main__":
    unittest.main()

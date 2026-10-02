import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from selfad.settings import validate_public_event_settings

SAFE_ENVIRONMENT = {
    "SELFAD_PUBLIC_EVENT_MODE": "true",
    "SELFAD_SECURE_COOKIES": "true",
    "SELFAD_TRUST_PROXY_HEADERS": "true",
    "SELFAD_TRUSTED_PROXY_HOSTS": "172.30.0.2",
    "SELFAD_GITEA_PUBLIC_URL": "https://git.ctf.example",
    "SELFAD_RUNNER_DOCKER_HOST": "tcp://runner.internal:2376",
    "SELFAD_RUNNER_TLS_VERIFY": "true",
    "SELFAD_RUNNER_CERT_PATH": "/run/selfad-runner-tls",
    "SELFAD_RUNNER_ISOLATION": "dedicated-host",
    "SELFAD_ENABLE_INTERNAL_RUNNER": "false",
    "SELFAD_SECRET_KEY": "s" * 48,
    "SELFAD_METRICS_TOKEN": "m" * 48,
    "SELFAD_SETUP_TOKEN": "u" * 48,
}


class PublicEventSettingsTests(unittest.TestCase):
    def test_safe_public_configuration_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("ca.pem", "cert.pem", "key.pem"):
                (Path(directory) / name).touch()
            environment = {
                **SAFE_ENVIRONMENT,
                "SELFAD_RUNNER_CERT_PATH": directory,
            }
            with patch.dict(os.environ, environment, clear=True):
                validate_public_event_settings(
                    "postgresql+psycopg://selfad@db/selfad"
                )

    def test_public_mode_rejects_development_boundaries(self):
        with patch.dict(
            os.environ,
            {"SELFAD_PUBLIC_EVENT_MODE": "true"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsafe public-event"):
                validate_public_event_settings("sqlite:///selfad.db")

    def test_public_mode_rejects_documented_placeholders(self):
        environment = {
            **SAFE_ENVIRONMENT,
            "SELFAD_SECRET_KEY": "replace-with-at-least-32-random-characters",
            "SELFAD_METRICS_TOKEN": "replace-with-a-long-random-token",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SELFAD_SECRET_KEY"):
                validate_public_event_settings(
                    "postgresql+psycopg://selfad:replace-with-password@db/selfad"
                )

    def test_private_mode_keeps_local_development_available(self):
        with patch.dict(os.environ, {}, clear=True):
            validate_public_event_settings("sqlite:///selfad.db")


if __name__ == "__main__":
    unittest.main()

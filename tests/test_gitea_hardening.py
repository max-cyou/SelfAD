import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class GiteaHardeningTests(unittest.TestCase):
    def test_image_disables_unneeded_participant_gitea_features(self):
        dockerfile = (PROJECT_DIR / "Dockerfile").read_text(encoding="utf-8")
        for setting in (
            "GITEA__repository__FORCE_PRIVATE=true",
            "GITEA__repository__MAX_CREATION_LIMIT=0",
            "GITEA__repository__DISABLE_HTTP_GIT=true",
            "GITEA__repository__DISABLE_MIGRATIONS=true",
            "GITEA__repository__upload__ENABLED=false",
            "GITEA__attachment__ENABLED=false",
            "GITEA__packages__ENABLED=false",
            "GITEA__service__ENABLE_CAPTCHA=true",
            "GITEA__service__REQUIRE_CAPTCHA_FOR_LOGIN=true",
        ):
            self.assertIn(setting, dockerfile)

    def test_ssh_transport_accepts_public_event_bursts(self):
        config = (PROJECT_DIR / "docker" / "sshd_config").read_text(
            encoding="utf-8"
        )
        dockerfile = (PROJECT_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("SSH_MAX_STARTUPS=100:30:200", dockerfile)
        self.assertIn("SSH_INCLUDE_FILE=/etc/ssh/selfad.conf", dockerfile)
        self.assertIn("LoginGraceTime 30", config)
        self.assertIn("PerSourceMaxStartups 50", config)

    def test_internal_api_uses_a_service_account_instead_of_root(self):
        service = (PROJECT_DIR / "docker" / "selfad-run").read_text(
            encoding="utf-8"
        )
        self.assertIn('service_username="selfad-system"', service)
        self.assertIn('--username "${service_username}"', service)
        self.assertIn('authenticated_user}" == "${service_username}', service)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_DIR / relative).read_text(encoding="utf-8")


class RegistrationErrorHandlingTests(unittest.TestCase):
    def test_registration_prechecks_reserved_gitea_usernames(self):
        participants = _read("selfad/routes/participants.py")
        gitea = _read("selfad/gitea.py")
        self.assertIn("def gitea_username_exists", gitea)
        self.assertIn("gitea_username_exists", participants)
        self.assertIn(
            "reserved Gitea accounts (root, the organiser)",
            participants,
        )

    def test_conflicts_map_to_form_fields_not_gateway_errors(self):
        participants = _read("selfad/routes/participants.py")
        admin = _read("selfad/routes/admin_users.py")
        self.assertIn('"username" if gitea_user is None', participants)
        self.assertIn('"username" if gitea_user is None', admin)
        # Only real backend unavailability may render a 502.
        self.assertIn("except GiteaUnavailable", participants)
        self.assertIn("except GiteaUnavailable", admin)

    def test_half_created_gitea_users_are_rolled_back(self):
        for route in (
            "selfad/routes/participants.py",
            "selfad/routes/admin_users.py",
        ):
            content = _read(route)
            self.assertIn("delete_gitea_user", content)


if __name__ == "__main__":
    unittest.main()

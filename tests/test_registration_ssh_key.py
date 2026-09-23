import unittest

from selfad.routes.participants import validate_registration


class RegistrationSshKeyTests(unittest.TestCase):
    def _values(self, ssh_public_key: str) -> dict[str, str]:
        return {
            "username": "participant",
            "email": "participant@example.test",
            "ssh_public_key": ssh_public_key,
        }

    def test_empty_ssh_key_is_allowed(self):
        errors = validate_registration(
            self._values(""),
            "correct-password",
            "correct-password",
        )

        self.assertNotIn("ssh_public_key", errors)

    def test_nonempty_invalid_ssh_key_is_rejected(self):
        errors = validate_registration(
            self._values("not-an-ssh-key"),
            "correct-password",
            "correct-password",
        )

        self.assertIn("ssh_public_key", errors)


if __name__ == "__main__":
    unittest.main()

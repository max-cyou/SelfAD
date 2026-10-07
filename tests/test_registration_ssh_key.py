import unittest

from selfad.routes.participants import validate_registration


class RegistrationTests(unittest.TestCase):
    def _values(self) -> dict[str, str]:
        return {
            "username": "participant",
            "email": "participant@example.test",
        }

    def test_registration_does_not_require_an_ssh_key(self):
        errors = validate_registration(
            self._values(),
            "correct-password",
            "correct-password",
        )

        self.assertEqual(errors, {})


if __name__ == "__main__":
    unittest.main()

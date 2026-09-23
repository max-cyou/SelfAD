import unittest

from selfad.repository_readmes import (
    attack_readme,
    issued_service_readme,
)


class ParticipantReadmeTests(unittest.TestCase):
    def test_attack_readme_shows_port_and_available_requirements(self):
        readme = attack_readme(
            "Notes",
            "main",
            8080,
            "requests==2.32.5\n# ignored\nbeautifulsoup4==4.14.3\n",
            True,
        ).decode()

        self.assertIn("SELFAD_TARGET=http://127.0.0.1:8080", readme)
        self.assertIn("`requests==2.32.5`", readme)
        self.assertIn("`beautifulsoup4==4.14.3`", readme)
        self.assertIn("You may add your own pinned packages", readme)
    def test_issued_service_readme_only_explains_local_start(self):
        readme = issued_service_readme("Notes", "notes", 8080).decode()

        self.assertIn("docker build -t selfad-notes .", readme)
        self.assertIn("docker run --rm -p 8080:8080 selfad-notes", readme)
        self.assertNotIn("defense", readme.lower())


if __name__ == "__main__":
    unittest.main()

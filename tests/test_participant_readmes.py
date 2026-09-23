import unittest

from selfad.repository_readmes import (
    attack_readme,
    defense_readme,
    organizer_readme,
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
        self.assertIn("README.txt", readme)

    def test_defense_readme_has_two_command_local_start(self):
        readme = defense_readme("Notes", "main", "notes", 8080).decode()

        self.assertIn("docker build -t selfad-notes .", readme)
        self.assertIn("docker run --rm -p 8080:8080 selfad-notes", readme)
        self.assertIn("replaces the source repository's README", readme)
        self.assertIn("README.txt", readme)

    def test_organizer_is_told_where_to_put_participant_notes(self):
        readme = organizer_readme("Notes", "main").decode()

        self.assertIn("README.txt", readme)
        self.assertIn("copied unchanged", readme)


if __name__ == "__main__":
    unittest.main()

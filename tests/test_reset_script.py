import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class ResetScriptTests(unittest.TestCase):
    def test_reset_requires_explicit_confirmation(self):
        script = (PROJECT_DIR / "scripts" / "ops" / "reset.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--yes", script)
        self.assertIn("Type 'delete' to confirm", script)
        self.assertIn("Aborted; nothing was deleted.", script)

    def test_reset_removes_only_the_data_volume(self):
        script = (PROJECT_DIR / "scripts" / "ops" / "reset.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("volume_name=selfad_selfad-data", script)
        self.assertIn("docker volume rm", script)
        self.assertNotIn("runner-certs", script.replace(
            "The runner certificates were kept.", ""
        ))
        self.assertIn("./scripts/ops/backup.sh", script)
        self.assertIn("./scripts/ops/install.sh", script)


if __name__ == "__main__":
    unittest.main()

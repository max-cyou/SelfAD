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
        ):
            self.assertIn(setting, dockerfile)


if __name__ == "__main__":
    unittest.main()

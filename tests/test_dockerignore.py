import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class DockerignoreTests(unittest.TestCase):
    def test_build_context_is_an_explicit_runtime_allowlist(self):
        dockerignore = (PROJECT_DIR / ".dockerignore").read_text(
            encoding="utf-8"
        )
        self.assertIn("\n*\n", dockerignore)
        for allowed_path in (
            "!Dockerfile",
            "!alembic.ini",
            "!migrations/**",
            "!requirements.txt",
            "!main.py",
            "!selfad/**",
            "!docker/**",
        ):
            self.assertIn(allowed_path, dockerignore)


if __name__ == "__main__":
    unittest.main()

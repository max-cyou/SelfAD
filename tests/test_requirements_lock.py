import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class RequirementsLockTests(unittest.TestCase):
    def test_every_runtime_requirement_is_pinned(self):
        requirements = (PROJECT_DIR / "requirements.txt").read_text(
            encoding="utf-8"
        )
        for line in requirements.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!~-]+$")


if __name__ == "__main__":
    unittest.main()

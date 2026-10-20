import unittest
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]


class CIConfigurationTests(unittest.TestCase):
    def test_postgresql_job_installs_testclient_dependencies(self):
        workflow = yaml.safe_load(
            (PROJECT_DIR / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        )
        steps = workflow["jobs"]["postgres"]["steps"]
        install = next(step["run"] for step in steps if "pip install" in step.get("run", ""))
        self.assertIn("-r requirements.txt", install)
        self.assertIn("-r requirements-dev.txt", install)
        dev_requirements = (PROJECT_DIR / "requirements-dev.txt").read_text(encoding="utf-8")
        self.assertIn("httpx2==", dev_requirements)


if __name__ == "__main__":
    unittest.main()

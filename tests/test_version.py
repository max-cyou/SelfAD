import tomllib
import unittest
from pathlib import Path

from selfad import __version__
from selfad.application import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_package_and_project_versions_match(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
            project_config = tomllib.load(file)

        self.assertEqual(project_config["tool"]["selfad"]["version"], __version__)

    def test_openapi_reports_release_version(self) -> None:
        self.assertEqual(create_app().version, __version__)

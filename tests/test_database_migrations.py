import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

PROJECT_DIR = Path(__file__).resolve().parents[1]


class DatabaseMigrationTests(unittest.TestCase):
    def _initialize(self, database_url: str) -> None:
        environment = os.environ.copy()
        environment["SELFAD_DATABASE_URL"] = database_url
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from selfad.database import initialize_database; initialize_database()",
            ],
            cwd=PROJECT_DIR,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_fresh_database_is_upgraded_to_head(self):
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'fresh.db'}"
            self._initialize(database_url)
            engine = create_engine(database_url)
            try:
                schema = inspect(engine)
                self.assertIn("alembic_version", schema.get_table_names())
                with engine.connect() as connection:
                    version = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(version, "0003")
                service_checks = {
                    check["name"]: check["sqltext"]
                    for check in schema.get_check_constraints("services")
                }
                self.assertIn("ready_to_issue", service_checks["service_status"])
                service_columns = {
                    column["name"]: column
                    for column in schema.get_columns("services")
                }
                self.assertEqual(service_columns["status"]["type"].length, 14)
            finally:
                engine.dispose()

    def test_existing_database_is_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'legacy.db'}"
            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE TABLE instance_config ("
                            "id INTEGER PRIMARY KEY, site_name VARCHAR(120) NOT NULL, "
                            "setup_complete BOOLEAN NOT NULL DEFAULT 0)"
                        )
                    )
                # The supported adoption path starts from a complete legacy
                # release schema. Build that shape without an Alembic stamp.
                from selfad import models
                from selfad.database import Base

                self.assertIsNotNone(models.Service.__table__)
                Base.metadata.create_all(engine)
            finally:
                engine.dispose()
            self._initialize(database_url)
            engine = create_engine(database_url)
            try:
                with engine.connect() as connection:
                    version = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(version, "0003")
                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("instance_config")
                }
                self.assertIn("gitea_public_enabled", columns)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()

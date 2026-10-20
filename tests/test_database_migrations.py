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
                self.assertEqual(version, "0005")
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

    def test_worker_recovery_upgrade_preserves_existing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'upgrade.db'}"
            self._initialize(database_url)
            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    for column in ("heartbeat_at", "retry_after", "scored_at"):
                        connection.execute(text(f"ALTER TABLE repository_events DROP COLUMN {column}"))
                    connection.execute(text("UPDATE alembic_version SET version_num = '0003'"))
                    connection.execute(text(
                        "INSERT INTO repository_events "
                        "(delivery_id, repository_path, ref, commit_sha, status, attempts, message, received_at) "
                        "VALUES ('upgrade', 'player/attack', 'refs/heads/main', 'abc', "
                        "'pending', 0, '', CURRENT_TIMESTAMP)"
                    ))
                self._initialize(database_url)
                columns = {column["name"] for column in inspect(engine).get_columns("repository_events")}
                self.assertTrue({"heartbeat_at", "retry_after", "scored_at"} <= columns)
                with engine.connect() as connection:
                    self.assertEqual(connection.scalar(text("SELECT COUNT(*) FROM repository_events")), 1)
            finally:
                engine.dispose()

    def test_admission_upgrade_and_downgrade_preserve_legacy_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'admission.db'}"
            self._initialize(database_url)
            environment = {**os.environ, 'SELFAD_DATABASE_URL': database_url}
            result = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '0004'],
                                    cwd=PROJECT_DIR, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            engine = create_engine(database_url)
            try:
                with engine.begin() as connection:
                    for delivery in ('one', 'two'):
                        connection.execute(text(
                            "INSERT INTO repository_events (delivery_id, repository_path, ref, "
                            "commit_sha, status, attempts, message, received_at) VALUES "
                            "(:delivery, 'player/attack', 'refs/heads/main', 'abc', 'done', 1, '', CURRENT_TIMESTAMP)"
                        ), {'delivery': delivery})
                self._initialize(database_url)
                with engine.connect() as connection:
                    self.assertEqual(connection.scalar(text('SELECT COUNT(*) FROM repository_events')), 2)
                columns = {column['name'] for column in inspect(engine).get_columns('repository_events')}
                self.assertTrue({'push_key', 'admission_json', 'admitted_at'} <= columns)
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
                self.assertEqual(version, "0005")
                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("instance_config")
                }
                self.assertIn("gitea_public_enabled", columns)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()

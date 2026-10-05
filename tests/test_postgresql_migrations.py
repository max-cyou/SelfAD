import os
import unittest

from sqlalchemy import inspect, text

DATABASE_URL = os.getenv("SELFAD_DATABASE_URL", "")


@unittest.skipUnless(
    DATABASE_URL.startswith("postgresql"),
    "PostgreSQL integration database is not configured",
)
class PostgreSQLMigrationTests(unittest.TestCase):
    def setUp(self):
        from selfad import models
        from selfad.database import Base, engine

        self.assertIsNotNone(models.Service.__table__)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        Base.metadata.drop_all(engine)

    def tearDown(self):
        from selfad.database import Base, engine

        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        Base.metadata.drop_all(engine)

    def _assert_at_head(self):
        from selfad.database import engine

        self.assertIn("alembic_version", inspect(engine).get_table_names())
        with engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0002",
            )

    def test_fresh_postgresql_database_reaches_head(self):
        from selfad.database import initialize_database

        initialize_database()
        self._assert_at_head()

    def test_unversioned_postgresql_database_is_adopted(self):
        from selfad.database import Base, engine, initialize_database

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            for table, column in (
                ("instance_config", "registration_enabled"),
                ("services", "runtime_status"),
                ("users", "gitea_username"),
                ("branding_settings", "homepage_html"),
                ("palette_settings", "success_color"),
                ("repository_events", "processing_token"),
                ("scoring_settings", "attack_requirements"),
                ("submission_attempts", "stdout_noise"),
                ("participant_services", "first_awarded_at"),
            ):
                connection.execute(
                    text(f"ALTER TABLE {table} DROP COLUMN {column}")
                )
        initialize_database()
        self._assert_at_head()
        schema = inspect(engine)
        service_columns = {
            item["name"]: item for item in schema.get_columns("services")
        }
        self.assertEqual(service_columns["status"]["type"].length, 14)
        for table, column in (
            ("instance_config", "registration_enabled"),
            ("services", "runtime_status"),
            ("users", "gitea_username"),
            ("branding_settings", "homepage_html"),
            ("palette_settings", "success_color"),
            ("repository_events", "processing_token"),
            ("scoring_settings", "attack_requirements"),
            ("submission_attempts", "stdout_noise"),
            ("participant_services", "first_awarded_at"),
        ):
            columns = {item["name"] for item in schema.get_columns(table)}
            self.assertIn(column, columns)


if __name__ == "__main__":
    unittest.main()

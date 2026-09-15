import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SELFAD_DATA_DIR", PROJECT_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "SELFAD_DATABASE_URL",
    f"sqlite:///{DATA_DIR / 'selfad.db'}",
)

connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def initialize_database() -> None:
    from selfad import models as _models

    Base.metadata.create_all(bind=engine)
    _migrate_existing_sqlite_schema()


def _migrate_existing_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return

    schema = inspect(engine)
    added_columns: set[str] = set()

    with engine.begin() as connection:
        instance_config_columns = {
            column["name"]
            for column in schema.get_columns("instance_config")
        }
        if "registration_enabled" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "registration_enabled BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "registration_invite_only" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "registration_invite_only BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "registration_invite_code_hash" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "registration_invite_code_hash VARCHAR(255)"
                )
            )
        if "contest_started" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "contest_started BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "contest_starts_at" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "contest_starts_at DATETIME"
                )
            )

        service_columns = {
            column["name"] for column in schema.get_columns("services")
        }
        service_column_types = {
            "repository_id": "INTEGER",
            "repository_path": "VARCHAR(255)",
            "jury_repository_id": "INTEGER",
            "jury_repository_path": "VARCHAR(255)",
            "validation_status": "VARCHAR(7) NOT NULL DEFAULT 'pending'",
            "validation_message": (
                "TEXT NOT NULL DEFAULT 'Repository contract has not been validated.'"
            ),
            "repository_generation": "INTEGER NOT NULL DEFAULT 0",
            "validated_source_commit": "VARCHAR(64)",
            "validated_jury_commit": "VARCHAR(64)",
            "container_port": "INTEGER",
            "healthcheck_path": "VARCHAR(512)",
            "validated_at": "DATETIME",
            "runtime_status": "VARCHAR(7) NOT NULL DEFAULT 'pending'",
            "runtime_message": (
                "TEXT NOT NULL DEFAULT 'Runtime check has not been started.'"
            ),
            "runtime_log": "TEXT NOT NULL DEFAULT ''",
            "runtime_matches": "INTEGER NOT NULL DEFAULT 0",
            "runtime_source_commit": "VARCHAR(64)",
            "runtime_jury_commit": "VARCHAR(64)",
            "runtime_checked_at": "DATETIME",
        }
        for column_name, column_type in service_column_types.items():
            if column_name not in service_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE services "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                added_columns.add(f"services.{column_name}")

        legacy_service_columns = {
            "gitlab_project_id": "repository_id",
            "gitlab_project_path": "repository_path",
            "jury_project_id": "jury_repository_id",
            "jury_project_path": "jury_repository_path",
        }
        for old_name, new_name in legacy_service_columns.items():
            if (
                old_name in service_columns
                and f"services.{new_name}" in added_columns
            ):
                connection.execute(
                    text(
                        f"UPDATE services SET {new_name} = {old_name} "
                        f"WHERE {new_name} IS NULL"
                    )
                )

        user_columns = {
            column["name"] for column in schema.get_columns("users")
        }
        if "ssh_public_key" not in user_columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN ssh_public_key VARCHAR(2048)")
            )
        if "git_ssh_key_id" not in user_columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN git_ssh_key_id INTEGER")
            )
            added_columns.add("users.git_ssh_key_id")
            if "gitlab_ssh_key_id" in user_columns:
                connection.execute(
                    text(
                        "UPDATE users SET git_ssh_key_id = gitlab_ssh_key_id "
                        "WHERE git_ssh_key_id IS NULL"
                    )
                )
        if "gitea_user_id" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN gitea_user_id INTEGER"))
            added_columns.add("users.gitea_user_id")
        if "gitea_username" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN gitea_username VARCHAR(32)"))
            added_columns.add("users.gitea_username")

        branding_columns = {
            column["name"] for column in schema.get_columns("branding_settings")
        }
        if "homepage_html" not in branding_columns:
            connection.execute(
                text(
                    "ALTER TABLE branding_settings "
                    "ADD COLUMN homepage_html TEXT NOT NULL DEFAULT ''"
                )
            )
        if "started_homepage_html" not in branding_columns:
            connection.execute(
                text(
                    "ALTER TABLE branding_settings "
                    "ADD COLUMN started_homepage_html TEXT NOT NULL DEFAULT ''"
                )
            )

        palette_columns = {
            column["name"] for column in schema.get_columns("palette_settings")
        }
        palette_column_defaults = {
            "success_color": "#287455",
            "success_soft_color": "#EEF6F1",
            "warning_color": "#9A6700",
            "warning_soft_color": "#FFF8C5",
            "focus_color": "#2563EB",
            "button_color": "#172033",
            "button_hover_color": "#354052",
            "button_text_color": "#FFFFFF",
            "input_color": "#FFFFFF",
            "input_disabled_color": "#FAFBFC",
            "table_heading_color": "#FAFBFC",
            "table_hover_color": "#FAFBFC",
            "table_selected_color": "#F3F6FA",
            "header_color": "#FFFFFF",
            "header_text_color": "#111827",
            "header_link_color": "#6B7280",
            "header_link_hover_color": "#111827",
            "home_color": "#FFFFFF",
            "home_title_color": "#111827",
            "home_text_color": "#6B7280",
            "home_link_color": "#4B5563",
            "home_link_hover_color": "#111827",
            "footer_text_color": "#9CA3AF",
            "footer_hover_color": "#6B7280",
        }
        for column_name, default in palette_column_defaults.items():
            if column_name not in palette_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE palette_settings ADD COLUMN {column_name} "
                        f"VARCHAR(7) NOT NULL DEFAULT '{default}'"
                    )
                )

        repository_event_columns = {
            column["name"]
            for column in schema.get_columns("repository_events")
        }
        repository_event_column_types = {
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "message": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, column_type in repository_event_column_types.items():
            if column_name not in repository_event_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE repository_events "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        if "services.repository_id" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_services_repository_id "
                    "ON services (repository_id) "
                    "WHERE repository_id IS NOT NULL"
                )
            )
        if "services.repository_path" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_services_repository_path "
                    "ON services (repository_path) "
                    "WHERE repository_path IS NOT NULL"
                )
            )
        if "services.jury_repository_id" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_services_jury_repository_id "
                    "ON services (jury_repository_id) "
                    "WHERE jury_repository_id IS NOT NULL"
                )
            )
        if "services.jury_repository_path" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_services_jury_repository_path "
                    "ON services (jury_repository_path) "
                    "WHERE jury_repository_path IS NOT NULL"
                )
            )
        if "users.git_ssh_key_id" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_users_git_ssh_key_id "
                    "ON users (git_ssh_key_id) "
                    "WHERE git_ssh_key_id IS NOT NULL"
                )
            )
        if "users.gitea_user_id" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_users_gitea_user_id "
                    "ON users (gitea_user_id) WHERE gitea_user_id IS NOT NULL"
                )
            )
        if "users.gitea_username" in added_columns:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_users_gitea_username "
                    "ON users (gitea_username) WHERE gitea_username IS NOT NULL"
                )
            )

        connection.execute(text("PRAGMA optimize"))

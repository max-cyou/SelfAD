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
    _migrate_existing_postgresql_schema()
    with SessionLocal() as session:
        if session.get(_models.ScoringSettings, 1) is None:
            session.add(_models.ScoringSettings(id=1))
            session.commit()


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
        if "contest_ended" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "contest_ended BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "contest_starts_at" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "contest_starts_at DATETIME"
                )
            )
        if "contest_ends_at" not in instance_config_columns:
            connection.execute(
                text(
                    "ALTER TABLE instance_config ADD COLUMN "
                    "contest_ends_at DATETIME"
                )
            )

        participant_columns = {
            column["name"] for column in schema.get_columns("participant_services")
        }
        participant_column_types = {
            "attack_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "defense_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "attack_best_raw": "INTEGER NOT NULL DEFAULT 0",
            "defense_best_raw": "INTEGER NOT NULL DEFAULT 0",
            "attack_penalty_attempts": "INTEGER NOT NULL DEFAULT 0",
            "defense_penalty_attempts": "INTEGER NOT NULL DEFAULT 0",
            "first_awarded_at": "DATETIME",
            "last_awarded_at": "DATETIME",
        }
        if any(
            name not in participant_columns
            for name in participant_column_types
        ):
            for name, column_type in participant_column_types.items():
                if name not in participant_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE participant_services ADD COLUMN "
                            f"{name} {column_type}"
                        )
                    )
            # Backfill the aggregates from the existing attempt history; the
            # tables are small enough for a one-off full scan.
            connection.execute(
                text(
                    """
                    UPDATE participant_services SET
                        attack_attempt_count = COALESCE((SELECT COUNT(*)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'attack'), 0),
                        defense_attempt_count = COALESCE((SELECT COUNT(*)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'defense'), 0),
                        attack_best_raw = COALESCE((SELECT MAX(a.raw_score)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'attack'), 0),
                        defense_best_raw = COALESCE((SELECT MAX(a.raw_score)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'defense'), 0),
                        attack_penalty_attempts = COALESCE((SELECT COUNT(*)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'attack'
                            AND a.penalty_eligible IS TRUE), 0),
                        defense_penalty_attempts = COALESCE((SELECT COUNT(*)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.kind = 'defense'
                            AND a.penalty_eligible IS TRUE), 0),
                        first_awarded_at = (SELECT MIN(a.created_at)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.awarded_score > 0),
                        last_awarded_at = (SELECT MAX(a.created_at)
                            FROM submission_attempts a WHERE
                            a.participant_service_id = participant_services.id
                            AND a.awarded_score > 0)
                    """
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
        if "ended_homepage_html" not in branding_columns:
            connection.execute(
                text(
                    "ALTER TABLE branding_settings "
                    "ADD COLUMN ended_homepage_html TEXT NOT NULL DEFAULT ''"
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
            "processing_token": "VARCHAR(64)",
            "processing_started_at": "DATETIME",
        }
        for column_name, column_type in repository_event_column_types.items():
            if column_name not in repository_event_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE repository_events "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        repository_event_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'repository_events'"
            )
        ).scalar() or ""
        if "'processing'" not in repository_event_sql:
            connection.execute(
                text(
                    "CREATE TABLE repository_events_new ("
                    "id INTEGER NOT NULL PRIMARY KEY, "
                    "delivery_id VARCHAR(255) NOT NULL UNIQUE, "
                    "repository_path VARCHAR(255) NOT NULL, "
                    "ref VARCHAR(512) NOT NULL, "
                    "commit_sha VARCHAR(64) NOT NULL, "
                    "status VARCHAR(10) NOT NULL, "
                    "attempts INTEGER NOT NULL DEFAULT 0, "
                    "processing_token VARCHAR(64), "
                    "processing_started_at DATETIME, "
                    "message TEXT NOT NULL DEFAULT '', "
                    "received_at DATETIME NOT NULL, "
                    "processed_at DATETIME, "
                    "CONSTRAINT repository_event_status "
                    "CHECK (status IN ('pending', 'processing', 'done', 'failed'))"
                    ")"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO repository_events_new ("
                    "id, delivery_id, repository_path, ref, commit_sha, status, "
                    "attempts, processing_token, processing_started_at, message, "
                    "received_at, processed_at"
                    ") SELECT id, delivery_id, repository_path, ref, commit_sha, "
                    "status, attempts, processing_token, processing_started_at, "
                    "message, received_at, processed_at FROM repository_events"
                )
            )
            connection.execute(text("DROP TABLE repository_events"))
            connection.execute(
                text("ALTER TABLE repository_events_new RENAME TO repository_events")
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX ix_repository_events_delivery_id "
                    "ON repository_events (delivery_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_repository_events_repository_path "
                    "ON repository_events (repository_path)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_repository_events_status "
                    "ON repository_events (status)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_repository_events_processing_token "
                    "ON repository_events (processing_token)"
                )
            )

        scoring_columns = {
            column["name"] for column in schema.get_columns("scoring_settings")
        }
        if "attack_requirements" not in scoring_columns:
            connection.execute(
                text(
                    "ALTER TABLE scoring_settings "
                    "ADD COLUMN attack_requirements TEXT NOT NULL DEFAULT ''"
                )
            )
        if "allow_user_attack_requirements" not in scoring_columns:
            connection.execute(
                text(
                    "ALTER TABLE scoring_settings "
                    "ADD COLUMN allow_user_attack_requirements "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "penalize_stdout_noise" not in scoring_columns:
            connection.execute(
                text(
                    "ALTER TABLE scoring_settings "
                    "ADD COLUMN penalize_stdout_noise BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "stdout_noise_mode" not in scoring_columns:
            connection.execute(
                text(
                    "ALTER TABLE scoring_settings "
                    "ADD COLUMN stdout_noise_mode VARCHAR(24) NOT NULL DEFAULT 'ignore'"
                )
            )
            connection.execute(
                text(
                    "UPDATE scoring_settings SET stdout_noise_mode = 'unsuccessful' "
                    "WHERE penalize_stdout_noise = 1"
                )
            )
        if "stdout_noise_penalty_percent" not in scoring_columns:
            connection.execute(
                text(
                    "ALTER TABLE scoring_settings "
                    "ADD COLUMN stdout_noise_penalty_percent FLOAT NOT NULL DEFAULT 1.0"
                )
            )

        submission_attempt_columns = {
            column["name"] for column in schema.get_columns("submission_attempts")
        }
        if "stdout_noise" not in submission_attempt_columns:
            connection.execute(
                text(
                    "ALTER TABLE submission_attempts "
                    "ADD COLUMN stdout_noise BOOLEAN NOT NULL DEFAULT 0"
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


def _migrate_existing_postgresql_schema() -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE instance_config ADD COLUMN IF NOT EXISTS "
                "contest_ends_at TIMESTAMP WITH TIME ZONE"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE scoring_settings ADD COLUMN IF NOT EXISTS "
                "penalize_stdout_noise BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE scoring_settings ADD COLUMN IF NOT EXISTS "
                "stdout_noise_mode VARCHAR(24) NOT NULL DEFAULT 'ignore'"
            )
        )
        connection.execute(
            text(
                "UPDATE scoring_settings SET stdout_noise_mode = 'unsuccessful' "
                "WHERE penalize_stdout_noise = TRUE AND stdout_noise_mode = 'ignore'"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE scoring_settings ADD COLUMN IF NOT EXISTS "
                "stdout_noise_penalty_percent DOUBLE PRECISION NOT NULL DEFAULT 1.0"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE submission_attempts ADD COLUMN IF NOT EXISTS "
                "stdout_noise BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        participant_columns = {
            "attack_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "defense_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "attack_best_raw": "INTEGER NOT NULL DEFAULT 0",
            "defense_best_raw": "INTEGER NOT NULL DEFAULT 0",
            "attack_penalty_attempts": "INTEGER NOT NULL DEFAULT 0",
            "defense_penalty_attempts": "INTEGER NOT NULL DEFAULT 0",
            "first_awarded_at": "TIMESTAMP WITH TIME ZONE",
            "last_awarded_at": "TIMESTAMP WITH TIME ZONE",
        }
        participant_columns_changed = False
        for name, column_type in participant_columns.items():
            column_exists = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'participant_services' AND column_name = :name"
                ),
                {"name": name},
            ).scalar() is not None
            connection.execute(
                text(
                    f"ALTER TABLE participant_services ADD COLUMN IF NOT EXISTS "
                    f"{name} {column_type}"
                )
            )
            participant_columns_changed = participant_columns_changed or not column_exists
        if participant_columns_changed:
            connection.execute(
                text(
                    """
                UPDATE participant_services SET
                    attack_attempt_count = COALESCE((SELECT COUNT(*)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'attack'), 0),
                    defense_attempt_count = COALESCE((SELECT COUNT(*)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'defense'), 0),
                    attack_best_raw = COALESCE((SELECT MAX(a.raw_score)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'attack'), 0),
                    defense_best_raw = COALESCE((SELECT MAX(a.raw_score)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'defense'), 0),
                    attack_penalty_attempts = COALESCE((SELECT COUNT(*)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'attack' AND a.penalty_eligible IS TRUE), 0),
                    defense_penalty_attempts = COALESCE((SELECT COUNT(*)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.kind = 'defense' AND a.penalty_eligible IS TRUE), 0),
                    first_awarded_at = (SELECT MIN(a.created_at)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.awarded_score > 0),
                    last_awarded_at = (SELECT MAX(a.created_at)
                        FROM submission_attempts a WHERE
                        a.participant_service_id = participant_services.id
                        AND a.awarded_score > 0)
                    """
                )
            )

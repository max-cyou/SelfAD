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
        service_columns = {
            column["name"] for column in schema.get_columns("services")
        }
        service_column_types = {
            "repository_id": "INTEGER",
            "repository_path": "VARCHAR(255)",
            "jury_repository_id": "INTEGER",
            "jury_repository_path": "VARCHAR(255)",
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

        connection.execute(text("PRAGMA optimize"))

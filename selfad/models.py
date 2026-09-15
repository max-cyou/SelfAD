from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from selfad.database import Base


class InstanceConfig(Base):
    __tablename__ = "instance_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_name: Mapped[str] = mapped_column(
        String(120),
        default="SelfAD",
        nullable=False,
    )
    setup_complete: Mapped[bool] = mapped_column(default=False, nullable=False)
    registration_enabled: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
    registration_invite_only: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
    registration_invite_code_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )


class BrandingSettings(Base):
    __tablename__ = "branding_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_title: Mapped[bool] = mapped_column(default=False, nullable=False)
    remove_standard_logo: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
    homepage_html: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )


class PaletteSettings(Base):
    __tablename__ = "palette_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_color: Mapped[str] = mapped_column(String(7), nullable=False)
    surface_color: Mapped[str] = mapped_column(String(7), nullable=False)
    surface_subtle_color: Mapped[str] = mapped_column(String(7), nullable=False)
    text_color: Mapped[str] = mapped_column(String(7), nullable=False)
    text_soft_color: Mapped[str] = mapped_column(String(7), nullable=False)
    muted_color: Mapped[str] = mapped_column(String(7), nullable=False)
    border_color: Mapped[str] = mapped_column(String(7), nullable=False)
    border_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    accent_color: Mapped[str] = mapped_column(String(7), nullable=False)
    accent_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    accent_contrast_color: Mapped[str] = mapped_column(String(7), nullable=False)
    danger_color: Mapped[str] = mapped_column(String(7), nullable=False)
    danger_soft_color: Mapped[str] = mapped_column(String(7), nullable=False)


class ServiceStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"


class ServiceValidationStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class RepositoryEventStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class ServiceRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class ParticipantRepositoryStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    repository_id: Mapped[int | None] = mapped_column(
        unique=True,
        nullable=True,
    )
    repository_path: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    jury_repository_id: Mapped[int | None] = mapped_column(
        unique=True,
        nullable=True,
    )
    jury_repository_path: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    default_branch: Mapped[str] = mapped_column(
        String(255),
        default="main",
        nullable=False,
    )
    status: Mapped[ServiceStatus] = mapped_column(
        SqlEnum(
            ServiceStatus,
            name="service_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value for item in enum_class
            ],
            create_constraint=True,
        ),
        default=ServiceStatus.DRAFT,
        index=True,
        nullable=False,
    )
    validation_status: Mapped[ServiceValidationStatus] = mapped_column(
        SqlEnum(
            ServiceValidationStatus,
            name="service_validation_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value for item in enum_class
            ],
            create_constraint=True,
        ),
        default=ServiceValidationStatus.PENDING,
        nullable=False,
    )
    validation_message: Mapped[str] = mapped_column(
        Text,
        default="Repository contract has not been validated.",
        nullable=False,
    )
    repository_generation: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )
    validated_source_commit: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    validated_jury_commit: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    container_port: Mapped[int | None] = mapped_column(nullable=True)
    healthcheck_path: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    runtime_status: Mapped[ServiceRunStatus] = mapped_column(
        SqlEnum(
            ServiceRunStatus,
            name="service_run_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            create_constraint=True,
        ),
        default=ServiceRunStatus.PENDING,
        nullable=False,
    )
    runtime_message: Mapped[str] = mapped_column(
        Text,
        default="Runtime check has not been started.",
        nullable=False,
    )
    runtime_log: Mapped[str] = mapped_column(Text, default="", nullable=False)
    runtime_matches: Mapped[int] = mapped_column(default=0, nullable=False)
    runtime_source_commit: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    runtime_jury_commit: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    runtime_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RepositoryEvent(Base):
    __tablename__ = "repository_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    repository_path: Mapped[str] = mapped_column(
        String(255),
        index=True,
        nullable=False,
    )
    ref: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RepositoryEventStatus] = mapped_column(
        SqlEnum(
            RepositoryEventStatus,
            name="repository_event_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            create_constraint=True,
        ),
        default=RepositoryEventStatus.PENDING,
        index=True,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    ssh_public_key: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    git_ssh_key_id: Mapped[int | None] = mapped_column(
        unique=True,
        nullable=True,
    )
    gitea_user_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    gitea_username: Mapped[str | None] = mapped_column(
        String(32),
        unique=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ParticipantService(Base):
    __tablename__ = "participant_services"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(index=True, nullable=False)
    attack_repository_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    attack_repository_path: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    defense_repository_id: Mapped[int | None] = mapped_column(unique=True, nullable=True)
    defense_repository_path: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    attack_dockerfile_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    defense_dockerfile_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    defense_unlocked: Mapped[bool] = mapped_column(default=False, nullable=False)
    attack_status: Mapped[ParticipantRepositoryStatus] = mapped_column(
        SqlEnum(ParticipantRepositoryStatus, name="participant_repository_status", native_enum=False, validate_strings=True, values_callable=lambda enum_class: [item.value for item in enum_class], create_constraint=True),
        default=ParticipantRepositoryStatus.READY,
        nullable=False,
    )
    defense_status: Mapped[ParticipantRepositoryStatus] = mapped_column(
        SqlEnum(ParticipantRepositoryStatus, name="participant_defense_status", native_enum=False, validate_strings=True, values_callable=lambda enum_class: [item.value for item in enum_class], create_constraint=True),
        default=ParticipantRepositoryStatus.READY,
        nullable=False,
    )
    attack_score: Mapped[int] = mapped_column(default=0, nullable=False)
    defense_score: Mapped[int] = mapped_column(default=0, nullable=False)
    attack_message: Mapped[str] = mapped_column(Text, default="Waiting for an exploit push.", nullable=False)
    defense_message: Mapped[str] = mapped_column(Text, default="Unlocks after a successful exploit.", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

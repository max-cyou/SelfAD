from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, String, Text
from sqlalchemy import Enum as SqlEnum
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
    contest_started: Mapped[bool] = mapped_column(default=False, nullable=False)
    contest_ended: Mapped[bool] = mapped_column(default=False, nullable=False)
    contest_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    contest_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
    started_homepage_html: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    ended_homepage_html: Mapped[str] = mapped_column(
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
    success_color: Mapped[str] = mapped_column(String(7), nullable=False)
    success_soft_color: Mapped[str] = mapped_column(String(7), nullable=False)
    warning_color: Mapped[str] = mapped_column(String(7), nullable=False)
    warning_soft_color: Mapped[str] = mapped_column(String(7), nullable=False)
    focus_color: Mapped[str] = mapped_column(String(7), nullable=False)
    button_color: Mapped[str] = mapped_column(String(7), nullable=False)
    button_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    button_text_color: Mapped[str] = mapped_column(String(7), nullable=False)
    input_color: Mapped[str] = mapped_column(String(7), nullable=False)
    input_disabled_color: Mapped[str] = mapped_column(String(7), nullable=False)
    table_heading_color: Mapped[str] = mapped_column(String(7), nullable=False)
    table_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    table_selected_color: Mapped[str] = mapped_column(String(7), nullable=False)
    header_color: Mapped[str] = mapped_column(String(7), nullable=False)
    header_text_color: Mapped[str] = mapped_column(String(7), nullable=False)
    header_link_color: Mapped[str] = mapped_column(String(7), nullable=False)
    header_link_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    home_color: Mapped[str] = mapped_column(String(7), nullable=False)
    home_title_color: Mapped[str] = mapped_column(String(7), nullable=False)
    home_text_color: Mapped[str] = mapped_column(String(7), nullable=False)
    home_link_color: Mapped[str] = mapped_column(String(7), nullable=False)
    home_link_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)
    footer_text_color: Mapped[str] = mapped_column(String(7), nullable=False)
    footer_hover_color: Mapped[str] = mapped_column(String(7), nullable=False)


class ServiceStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"


class ServiceValidationStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class RepositoryEventStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
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


class ScoringSettings(Base):
    __tablename__ = "scoring_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    attack_reward_mode: Mapped[str] = mapped_column(
        String(16), default="coverage", nullable=False
    )
    attack_max_points: Mapped[int] = mapped_column(default=100, nullable=False)
    attack_points_per_flag: Mapped[int] = mapped_column(default=10, nullable=False)
    defense_reward_mode: Mapped[str] = mapped_column(
        String(16), default="per_flag", nullable=False
    )
    defense_max_points: Mapped[int] = mapped_column(default=100, nullable=False)
    defense_points_lost_per_flag: Mapped[int] = mapped_column(
        default=10, nullable=False
    )
    penalty_mode: Mapped[str] = mapped_column(
        String(16), default="percent", nullable=False
    )
    attack_penalty_value: Mapped[float] = mapped_column(
        default=5.0, nullable=False
    )
    defense_penalty_value: Mapped[float] = mapped_column(
        default=5.0, nullable=False
    )
    attack_free_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    defense_free_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    penalize_check_errors: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Kept for an in-place upgrade from releases which exposed this as a checkbox.
    penalize_stdout_noise: Mapped[bool] = mapped_column(default=False, nullable=False)
    stdout_noise_mode: Mapped[str] = mapped_column(
        String(24), default="ignore", nullable=False
    )
    stdout_noise_penalty_percent: Mapped[float] = mapped_column(
        default=1.0, nullable=False
    )
    attack_requirements: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    allow_user_attack_requirements: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )


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
    processing_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
    # Denormalised attempt aggregates: scoring and the scoreboard read these
    # instead of scanning submission_attempts, which is pruned.
    attack_attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    defense_attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    attack_best_raw: Mapped[int] = mapped_column(default=0, nullable=False)
    defense_best_raw: Mapped[int] = mapped_column(default=0, nullable=False)
    attack_penalty_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    defense_penalty_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    first_awarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_awarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attack_message: Mapped[str] = mapped_column(Text, default="Waiting for an exploit push.", nullable=False)
    defense_message: Mapped[str] = mapped_column(Text, default="Unlocks after a successful exploit.", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_service_id: Mapped[int] = mapped_column(index=True, nullable=False)
    repository_path: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    matched_flags: Mapped[int] = mapped_column(default=0, nullable=False)
    injected_flags: Mapped[int] = mapped_column(default=0, nullable=False)
    functionality_passed: Mapped[bool] = mapped_column(default=False, nullable=False)
    completed: Mapped[bool] = mapped_column(default=False, nullable=False)
    improved: Mapped[bool] = mapped_column(default=False, nullable=False)
    penalty_eligible: Mapped[bool] = mapped_column(default=False, nullable=False)
    stdout_noise: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_score: Mapped[int] = mapped_column(default=0, nullable=False)
    penalty: Mapped[int] = mapped_column(default=0, nullable=False)
    awarded_score: Mapped[int] = mapped_column(default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

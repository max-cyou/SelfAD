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


class BrandingSettings(Base):
    __tablename__ = "branding_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_title: Mapped[bool] = mapped_column(default=False, nullable=False)
    remove_standard_logo: Mapped[bool] = mapped_column(
        default=False,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

"""Add the public Gitea web-interface switch."""

from alembic import op
from sqlalchemy import Boolean, Column, false, inspect

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("instance_config")
    }
    if "gitea_public_enabled" not in columns:
        op.add_column(
            "instance_config",
            Column(
                "gitea_public_enabled",
                Boolean(),
                nullable=False,
                server_default=false(),
            ),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("instance_config")
    }
    if "gitea_public_enabled" in columns:
        op.drop_column("instance_config", "gitea_public_enabled")

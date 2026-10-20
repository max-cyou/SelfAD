"""Persist worker heartbeats, delayed retries and scoring fences."""

from alembic import op
from sqlalchemy import Column, DateTime, inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("repository_events")}
    for name in ("heartbeat_at", "retry_after", "scored_at"):
        if name not in columns:
            op.add_column("repository_events", Column(name, DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("scored_at", "retry_after", "heartbeat_at"):
        op.drop_column("repository_events", name)

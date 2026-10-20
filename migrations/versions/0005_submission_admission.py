"""Persist immutable admission context and logical push identity."""

from alembic import op
from sqlalchemy import Column, DateTime, String, Text, inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    schema = inspect(op.get_bind())
    columns = {column["name"] for column in schema.get_columns("repository_events")}
    for column in (
        Column("admitted_at", DateTime(timezone=True), nullable=True),
        Column("admission_json", Text(), nullable=True),
        Column("push_key", String(64), nullable=True),
    ):
        if column.name not in columns:
            op.add_column("repository_events", column)
    indexes = schema.get_indexes("repository_events")
    constraints = schema.get_unique_constraints("repository_events")
    if not any(item.get("column_names") == ["push_key"] for item in indexes + constraints):
        op.create_index(
            "ix_repository_events_push_key", "repository_events", ["push_key"], unique=True
        )
    if "locked_rules_json" not in {
        column["name"] for column in schema.get_columns("scoring_settings")
    }:
        op.add_column("scoring_settings", Column("locked_rules_json", Text(), nullable=True))


def downgrade():
    indexes = inspect(op.get_bind()).get_indexes("repository_events")
    if any(index["name"] == "ix_repository_events_push_key" for index in indexes):
        op.drop_index("ix_repository_events_push_key", table_name="repository_events")
    for name in ("push_key", "admission_json", "admitted_at"):
        op.drop_column("repository_events", name)
    op.drop_column("scoring_settings", "locked_rules_json")

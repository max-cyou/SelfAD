"""Add the private ready-to-issue service stage."""

from alembic import op
from sqlalchemy import String

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_constraint("service_status", type_="check")
        batch_op.alter_column(
            "status",
            existing_type=String(length=6),
            type_=String(length=14),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "service_status",
            "status IN ('draft', 'ready_to_issue', 'active')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE services SET status = 'draft' "
        "WHERE status = 'ready_to_issue'"
    )
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_constraint("service_status", type_="check")
        batch_op.alter_column(
            "status",
            existing_type=String(length=14),
            type_=String(length=6),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "service_status",
            "status IN ('draft', 'active')",
        )

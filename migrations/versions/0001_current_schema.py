"""Establish the current SelfAD schema baseline."""

from alembic import op

from selfad import models as _models  # noqa: F401
from selfad.database import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())

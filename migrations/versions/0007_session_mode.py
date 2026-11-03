"""study session mode

Revision ID: 0007
Revises: 0006
"""

# pylint: disable=invalid-name  # alembic: revision ids
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.add_column("study_sessions", sa.Column("mode", sa.String(), server_default="mixed", nullable=False))


def downgrade() -> None:
    op.drop_column("study_sessions", "mode")

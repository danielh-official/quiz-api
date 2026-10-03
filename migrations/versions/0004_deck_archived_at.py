"""deck archived_at

Revision ID: 0004
Revises: 0003
"""

# pylint: disable=invalid-name  # alembic: revision ids
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("decks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("decks", "archived_at")

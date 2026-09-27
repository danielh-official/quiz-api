"""study session summary

Revision ID: 0002
Revises: 0001
"""

# pylint: disable=invalid-name,duplicate-code  # alembic: revision ids; repeated column shapes are normal
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column("study_sessions", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("study_sessions", "summary")

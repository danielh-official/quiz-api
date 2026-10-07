"""review pre_answer_note

Revision ID: 0005
Revises: 0004
"""

# pylint: disable=invalid-name  # alembic: revision ids
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column("reviews", sa.Column("pre_answer_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "pre_answer_note")

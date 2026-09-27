"""exams updated_at and completed_at

Revision ID: 0003
Revises: 6fc91e1a0924
"""

# pylint: disable=invalid-name  # alembic requires these module and variable names
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "6fc91e1a0924"


def upgrade() -> None:
    op.add_column("exams", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "exams",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("exams", "updated_at")
    op.drop_column("exams", "completed_at")

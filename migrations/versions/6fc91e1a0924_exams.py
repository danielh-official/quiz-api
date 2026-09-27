"""exams

Revision ID: 6fc91e1a0924
Revises: 0002
"""

# pylint: disable=invalid-name  # alembic requires these module and variable names
from alembic import op
import sqlalchemy as sa


revision = "6fc91e1a0924"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "exams",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.Index("idx_exams_user_id", "user_id"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "deck_exams",
        sa.Column("exam_id", sa.BigInteger(), nullable=False),
        sa.Column("deck_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["exam_id"], ["exams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deck_id"], ["decks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("exam_id", "deck_id"),
    )


def downgrade() -> None:
    op.drop_table("deck_exams")
    op.drop_table("exams")

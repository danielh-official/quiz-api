"""SQL practice: topics, problems, test cases, submissions

Revision ID: 0005
Revises: 0004
"""

# pylint: disable=invalid-name  # alembic: revision ids
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"


def created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "sql_topics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        created_at(),
        sa.UniqueConstraint("user_id", "slug"),
    )
    op.create_table(
        "sql_problems",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), sa.ForeignKey("sql_topics.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tables", postgresql.JSONB(), nullable=False),
        sa.Column("reference_query", sa.Text(), nullable=False),
        sa.Column("reference_dialect", sa.String(length=16), server_default="sqlite", nullable=False),
        sa.Column("order_matters", sa.Boolean(), server_default="false", nullable=False),
        created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("topic_id", "slug"),
    )
    op.create_table(
        "sql_test_cases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "problem_id", sa.BigInteger(), sa.ForeignKey("sql_problems.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("hidden", sa.Boolean(), server_default="false", nullable=False),
        created_at(),
    )
    op.create_table(
        "sql_submissions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("problem_id", sa.BigInteger(), sa.ForeignKey("sql_problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dialect", sa.String(length=16), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        created_at(),
        sa.Index("ix_sql_submissions_problem_created", "problem_id", "created_at"),
    )


def downgrade() -> None:
    op.drop_table("sql_submissions")
    op.drop_table("sql_test_cases")
    op.drop_table("sql_problems")
    op.drop_table("sql_topics")

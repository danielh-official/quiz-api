"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

# pylint: disable=invalid-name,duplicate-code  # alembic: revision ids; repeated column shapes are normal
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}

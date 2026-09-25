"""initial

Revision ID: 0001
Revises: 
"""

# pylint: disable=invalid-name  # alembic requires these module and variable names
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('login', sa.String(length=255), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('desired_retention', sa.Float(), server_default='0.9', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('desired_retention BETWEEN 0.7 AND 0.99', name='desired_retention_range'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'subject')
    )
    op.create_table('decks',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('session_size', sa.SmallInteger(), server_default='20', nullable=False),
    sa.Column('new_per_day', sa.SmallInteger(), server_default='20', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('new_per_day BETWEEN 0 AND 1000', name='new_per_day_range'),
    sa.CheckConstraint('session_size BETWEEN 1 AND 500', name='session_size_range'),
    sa.ForeignKeyConstraint(['parent_id'], ['decks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_decks_parent_id'), 'decks', ['parent_id'], unique=False)
    op.create_index(op.f('ix_decks_user_id'), 'decks', ['user_id'], unique=False)
    op.create_table('questions',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('deck_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('stem', sa.Text(), nullable=False),
    sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("type IN ('single', 'select_two')", name='question_type'),
    sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_questions_deck_id'), 'questions', ['deck_id'], unique=False)
    op.create_table('study_sessions',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('deck_id', sa.BigInteger(), nullable=False),
    sa.Column('size', sa.SmallInteger(), nullable=False),
    sa.Column('answered', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_study_sessions_user_id'), 'study_sessions', ['user_id'], unique=False)
    op.create_table('cards',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('question_id', sa.BigInteger(), nullable=False),
    sa.Column('stability', sa.Float(), nullable=True),
    sa.Column('difficulty', sa.Float(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reps', sa.Integer(), server_default='0', nullable=False),
    sa.Column('lapses', sa.Integer(), server_default='0', nullable=False),
    sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'question_id')
    )
    op.create_index(op.f('ix_cards_question_id'), 'cards', ['question_id'], unique=False)
    op.create_index('ix_cards_user_due', 'cards', ['user_id', 'due_at'], unique=False)
    op.create_table('reviews',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('question_id', sa.BigInteger(), nullable=False),
    sa.Column('study_session_id', sa.BigInteger(), nullable=True),
    sa.Column('selected', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('correct', sa.Boolean(), nullable=False),
    sa.Column('confidence', sa.String(length=16), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('was_new', sa.Boolean(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['study_session_id'], ['study_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reviews_question_id'), 'reviews', ['question_id'], unique=False)
    op.create_index(op.f('ix_reviews_study_session_id'), 'reviews', ['study_session_id'], unique=False)
    op.create_index('ix_reviews_user_reviewed', 'reviews', ['user_id', 'reviewed_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reviews_user_reviewed', table_name='reviews')
    op.drop_index(op.f('ix_reviews_study_session_id'), table_name='reviews')
    op.drop_index(op.f('ix_reviews_question_id'), table_name='reviews')
    op.drop_table('reviews')
    op.drop_index('ix_cards_user_due', table_name='cards')
    op.drop_index(op.f('ix_cards_question_id'), table_name='cards')
    op.drop_table('cards')
    op.drop_index(op.f('ix_study_sessions_user_id'), table_name='study_sessions')
    op.drop_table('study_sessions')
    op.drop_index(op.f('ix_questions_deck_id'), table_name='questions')
    op.drop_table('questions')
    op.drop_index(op.f('ix_decks_user_id'), table_name='decks')
    op.drop_index(op.f('ix_decks_parent_id'), table_name='decks')
    op.drop_table('decks')
    op.drop_table('users')

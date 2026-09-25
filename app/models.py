from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.schemas import QuestionType


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True), int: BigInteger, str: Text}


def fk(target: str, ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey(target, ondelete=ondelete)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "subject"),
        CheckConstraint("desired_retention BETWEEN 0.7 AND 0.99", name="desired_retention_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    login: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None]
    email: Mapped[str | None]
    timezone: Mapped[str] = mapped_column(String(64), server_default="UTC")
    desired_retention: Mapped[float] = mapped_column(Float, server_default="0.9")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Deck(Base):
    __tablename__ = "decks"
    __table_args__ = (
        CheckConstraint("session_size BETWEEN 1 AND 500", name="session_size_range"),
        CheckConstraint("new_per_day BETWEEN 0 AND 1000", name="new_per_day_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(fk("users.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(fk("decks.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None]
    session_size: Mapped[int] = mapped_column(SmallInteger, server_default="20")
    new_per_day: Mapped[int] = mapped_column(SmallInteger, server_default="20")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (CheckConstraint("type IN ('single', 'select_two')", name="question_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    deck_id: Mapped[int] = mapped_column(fk("decks.id"), index=True)
    type: Mapped[QuestionType] = mapped_column(String(16))
    stem: Mapped[str]
    # [{id, text, correct, explanation?}] — option ids are stable across edits.
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    explanation: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Card(Base):
    """One user's spaced-repetition state for one question. Created lazily."""

    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("user_id", "question_id"), Index("ix_cards_user_due", "user_id", "due_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(fk("users.id"))
    question_id: Mapped[int] = mapped_column(fk("questions.id"), index=True)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    due_at: Mapped[datetime | None]
    last_reviewed_at: Mapped[datetime | None]
    reps: Mapped[int] = mapped_column(Integer, server_default="0")
    lapses: Mapped[int] = mapped_column(Integer, server_default="0")
    suspended_at: Mapped[datetime | None]
    note: Mapped[str | None]


class StudySession(Base):
    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(fk("users.id"), index=True)
    deck_id: Mapped[int] = mapped_column(fk("decks.id"))
    size: Mapped[int] = mapped_column(SmallInteger)
    answered: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    finished_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Review(Base):
    """Append-only answer log (kept so an FSRS optimizer can be fitted later)."""

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_user_reviewed", "user_id", "reviewed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(fk("users.id"))
    question_id: Mapped[int] = mapped_column(fk("questions.id"), index=True)
    study_session_id: Mapped[int | None] = mapped_column(fk("study_sessions.id", "SET NULL"), index=True)
    selected: Mapped[list[str]] = mapped_column(JSONB)
    correct: Mapped[bool]
    confidence: Mapped[str] = mapped_column(String(16))
    rating: Mapped[int] = mapped_column(SmallInteger)
    was_new: Mapped[bool]
    reviewed_at: Mapped[datetime]

import os
from collections.abc import Container, Iterator
from typing import Any

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://quiz:secret@localhost:5433/test")
for name in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
    os.environ.pop(name, None)  # auth stays off; tests stub the token layer

# pylint: disable=wrong-import-position  # env vars above must be set before app modules import config
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from app import auth as auth_module, config, db as db_module, mcp as mcp_module
from app.models import Deck, Question, User
from app.schemas import AnswerIn, Confidence, DeckCreate, OptionIn, QuestionIn, QuestionType
from app.services import content, study


@pytest.fixture(scope="session", autouse=True)
def schema() -> None:
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Every test runs inside one outer transaction that is rolled back; commits become savepoints."""
    connection = db_module.engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(mcp_module, "SessionLocal", factory)
    monkeypatch.setattr(study, "FUZZ", False)
    monkeypatch.setattr(config, "ALLOWED_USERS", {"alice", "bob"})
    monkeypatch.setattr(auth_module, "MOCK", False)  # test real auth; mock tests switch it back on
    session = factory()
    yield session
    session.close()
    outer.rollback()
    connection.close()


def make_user(db: Session, login: str = "alice", subject: str = "1") -> User:
    user = User(provider="github", subject=subject, login=login, timezone="UTC", desired_retention=0.9)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def user(db: Session) -> User:
    return make_user(db)


@pytest.fixture
def other(db: Session) -> User:
    return make_user(db, "bob", "2")


def question_in(stem: str = "Q", type: QuestionType = "single", correct: Container[int] | None = None) -> QuestionIn:
    count, picks = {"single": (4, 1), "select_two": (5, 2)}[type]
    correct = range(picks) if correct is None else correct
    return QuestionIn(
        type=type,
        stem=stem,
        options=[OptionIn(text=f"{stem} option {i}", correct=i in correct, explanation=f"why {i}") for i in range(count)],
        explanation="because",
    )


def make_deck(db: Session, user: User, name: str = "Deck", **fields: Any) -> Deck:
    return content.create_deck(db, user, DeckCreate(name=name, **fields))


def make_questions(
    db: Session, user: User, deck: Deck, n: int = 1, prefix: str = "Q", type: QuestionType = "single"
) -> list[Question]:
    return content.create_questions(db, user, deck.id, [question_in(f"{prefix}{i}", type) for i in range(n)])


def answer(
    db: Session, user: User, session_id: int, question: Question, right: bool = True, confidence: Confidence = "confident"
) -> dict[str, Any]:
    wanted = [o for o in question.options if o["correct"] == right]
    pick = 1 if question.type == "single" else 2
    selected = [o["id"] for o in wanted[:pick]]
    return study.submit_answer(db, user, session_id, AnswerIn(question_id=question.id, selected=selected, confidence=confidence))

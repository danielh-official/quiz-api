import os

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://quiz:secret@localhost:5433/test")
for name in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
    os.environ.pop(name, None)  # auth stays off; tests stub the token layer

# pylint: disable=wrong-import-position  # env vars above must be set before app modules import config
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from app import config, db as db_module, mcp as mcp_module
from app.models import User
from app.schemas import AnswerIn, DeckCreate, OptionIn, QuestionIn
from app.services import content, study


@pytest.fixture(scope="session", autouse=True)
def schema():
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def db(monkeypatch):
    """Every test runs inside one outer transaction that is rolled back; commits become savepoints."""
    connection = db_module.engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(mcp_module, "SessionLocal", factory)
    monkeypatch.setattr(study, "FUZZ", False)
    monkeypatch.setattr(config, "ALLOWED_USERS", {"alice", "bob"})
    session = factory()
    yield session
    session.close()
    outer.rollback()
    connection.close()


def make_user(db, login="alice", subject="1") -> User:
    user = User(provider="github", subject=subject, login=login, timezone="UTC", desired_retention=0.9)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def user(db) -> User:
    return make_user(db)


@pytest.fixture
def other(db) -> User:
    return make_user(db, "bob", "2")


def question_in(stem="Q", type="single", correct=None) -> QuestionIn:
    count, picks = {"single": (4, 1), "select_two": (5, 2)}[type]
    correct = range(picks) if correct is None else correct
    return QuestionIn(
        type=type,
        stem=stem,
        options=[OptionIn(text=f"{stem} option {i}", correct=i in correct, explanation=f"why {i}") for i in range(count)],
        explanation="because",
    )


def make_deck(db, user, name="Deck", **fields):
    return content.create_deck(db, user, DeckCreate(name=name, **fields))


def make_questions(db, user, deck, n=1, prefix="Q", type="single"):
    return content.create_questions(db, user, deck.id, [question_in(f"{prefix}{i}", type) for i in range(n)])


def answer(db, user, session_id, question, right=True, confidence="confident"):
    wanted = [o for o in question.options if o["correct"] == right]
    pick = 1 if question.type == "single" else 2
    selected = [o["id"] for o in wanted[:pick]]
    return study.submit_answer(db, user, session_id, AnswerIn(question_id=question.id, selected=selected, confidence=confidence))

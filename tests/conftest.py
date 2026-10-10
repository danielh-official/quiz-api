import os
from collections.abc import Container, Iterator
from typing import Any

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://quiz:secret@localhost:5433/test")
for name in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "PLUGIN_MARKETPLACE"):
    os.environ.pop(name, None)  # auth stays off; tests stub the token layer; home page hides plugin install

# pylint: disable=wrong-import-position  # env vars above must be set before app modules import config
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from app import auth as auth_module, config, db as db_module, mcp as mcp_module
from app.models import Deck, Question, SqlProblem, User
from app.schemas import (
    AnswerIn,
    Confidence,
    DeckCreate,
    OptionIn,
    QuestionIn,
    QuestionType,
    SqlProblemCreate,
    SqlTopicCreate,
)
from app.services import NotFound, content, sql, study


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


EMPLOYEES = [
    {
        "name": "employees",
        "columns": [
            {"name": "id", "type": "integer"},
            {"name": "name", "type": "text"},
            {"name": "salary", "type": "number"},
            {"name": "hired", "type": "date"},
        ],
    }
]


def sql_problem_in(**fields: Any) -> SqlProblemCreate:
    """Second-highest distinct salary; the hidden case has a tie at the top, which a plain OFFSET 1 gets wrong."""
    values: dict[str, Any] = {
        "title": "Second highest salary",
        "description": "Return the **second highest** distinct salary as `salary`.",
        "tables": EMPLOYEES,
        "reference_query": "SELECT MAX(salary) AS salary FROM employees WHERE salary < (SELECT MAX(salary) FROM employees)",
        "test_cases": [
            {"data": {"employees": [[1, "Ann", 100, "2020-01-05"], [2, "Bob", 90, "2021-03-01"], [3, "Cy", 80, "2019-07-07"]]}},
            {
                "data": {
                    "employees": [[1, "Ann", 100, "2020-01-05"], [2, "Bob", 100, "2021-03-01"], [3, "Cy", 80, "2019-07-07"]]
                },
                "hidden": True,
            },
        ],
    }
    return SqlProblemCreate.model_validate({**values, **fields})


def make_sql_problem(db: Session, user: User, topic: str = "Aggregates", **fields: Any) -> SqlProblem:
    slug = sql.slugify(topic)
    try:
        sql.get_topic(db, user, slug)
    except NotFound:
        sql.create_topic(db, user, SqlTopicCreate(name=topic))
    return sql.create_problem(db, user, slug, sql_problem_in(**fields))

from collections.abc import Iterator

import pytest
from conftest import make_deck, make_questions
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth as auth_module
from app.main import app
from app.models import User
from app.web_app import web_claims


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def login_web(user: User | None = None, login: str = "alice", sub: str = "1", name: str = "Alice") -> None:
    if user is not None:
        login, sub, name = user.login, user.subject, user.name or user.login
    app.dependency_overrides[web_claims] = lambda: {"sub": sub, "login": login, "name": name, "email": None}


def test_app_stylesheet_is_served(client: TestClient) -> None:
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "bg-accent" in css.text or "--color-accent" in css.text


def test_app_requires_auth_when_not_mocked(client: TestClient) -> None:
    response = client.get("/app")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/app/login")


def test_app_mocked_needs_no_cookie(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "MOCK", True)
    page = client.get("/app")
    assert page.status_code == 200
    assert "Decks" in page.text
    assert "dev" in page.text


def test_login_mocked_redirects_to_app(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "MOCK", True)
    response = client.get("/app/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/app"


def test_decks_and_detail(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    parent = make_deck(db, user, "AWS")
    child = make_deck(db, user, "S3", parent_id=parent.id)
    make_questions(db, user, child, n=2, prefix="Q")

    decks = client.get("/app")
    assert decks.status_code == 200
    assert "AWS" in decks.text and "S3" in decks.text
    assert "2 new" in decks.text or "2 total" in decks.text

    detail = client.get(f"/app/decks/{parent.id}")
    assert detail.status_code == 200
    assert "Start session" in detail.text
    assert "S3" in detail.text
    assert "Performance" in detail.text


def test_study_loop_over_html(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "AWS", session_size=1)
    questions = make_questions(db, user, deck, n=1, prefix="S3")
    question = questions[0]

    started = client.post(f"/app/decks/{deck.id}/sessions")
    assert started.status_code == 303
    session_path = started.headers["location"]
    assert session_path.startswith("/app/sessions/")

    page = client.get(session_path)
    assert page.status_code == 200
    assert "Question 1 of 1" in page.text
    assert "S30" in page.text  # stem prefix from make_questions

    correct = [o["id"] for o in question.options if o["correct"]]
    result = client.post(
        f"{session_path}/answers",
        data={"question_id": str(question.id), "selected": correct, "confidence": "confident"},
    )
    assert result.status_code == 200
    assert "Correct" in result.text
    assert "Next" in result.text

    finished = client.get(f"{session_path}/next")
    assert finished.status_code == 200
    assert "Session finished" in finished.text
    assert "answered" in finished.text


def test_study_htmx_partials(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "AWS", session_size=1)
    question = make_questions(db, user, deck, n=1)[0]
    session_id = client.post(f"/app/decks/{deck.id}/sessions").headers["location"].rsplit("/", 1)[-1]
    correct = [o["id"] for o in question.options if o["correct"]]

    result = client.post(
        f"/app/sessions/{session_id}/answers",
        data={"question_id": str(question.id), "selected": correct, "confidence": "educated_guess"},
        headers={"HX-Request": "true"},
    )
    assert result.status_code == 200
    assert "<html" not in result.text.lower()
    assert "Correct" in result.text

    nxt = client.get(f"/app/sessions/{session_id}/next", headers={"HX-Request": "true"})
    assert nxt.status_code == 200
    assert "<html" not in nxt.text.lower()
    assert "Session finished" in nxt.text


def test_wrong_pick_count_redisplays_question(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "AWS", session_size=1)
    question = make_questions(db, user, deck, n=1, type="select_two")[0]
    session_id = client.post(f"/app/decks/{deck.id}/sessions").headers["location"].rsplit("/", 1)[-1]

    bad = client.post(
        f"/app/sessions/{session_id}/answers",
        data={"question_id": str(question.id), "selected": question.options[0]["id"], "confidence": "confident"},
        headers={"HX-Request": "true"},
    )
    assert bad.status_code == 422
    assert "Pick exactly" in bad.text


def test_other_users_deck_is_404_html(client: TestClient, db: Session, user: User, other: User) -> None:
    login_web(user)
    deck = make_deck(db, other, "Secret")
    response = client.get(f"/app/decks/{deck.id}")
    assert response.status_code == 404
    assert "Not found" in response.text


def test_deck_crud(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    form = client.get("/app/decks/new")
    assert form.status_code == 200
    assert "New deck" in form.text

    created = client.post(
        "/app/decks/new",
        data={"name": "AWS", "description": "Cloud", "parent_id": "", "session_size": "10", "new_per_day": "5"},
    )
    assert created.status_code == 303
    deck_path = created.headers["location"]
    assert deck_path.startswith("/app/decks/")
    deck_id = int(deck_path.rsplit("/", 1)[-1])

    detail = client.get(deck_path)
    assert detail.status_code == 200
    assert "AWS" in detail.text
    assert "Edit deck" in detail.text

    edited = client.post(
        f"/app/decks/{deck_id}/edit",
        data={
            "name": "AWS Certified",
            "description": "Cloud",
            "parent_id": "",
            "session_size": "15",
            "new_per_day": "5",
        },
    )
    assert edited.status_code == 303
    assert "AWS Certified" in client.get(deck_path).text

    deleted = client.post(f"/app/decks/{deck_id}/delete")
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/app"
    assert client.get(deck_path).status_code == 404


def test_safe_next_rejects_external(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "MOCK", True)
    response = client.get("/app/login", params={"next": "https://evil.example/"})
    assert response.status_code == 303
    assert response.headers["location"] == "/app"

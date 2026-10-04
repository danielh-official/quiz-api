from collections.abc import Iterator

import pytest
from conftest import make_deck, make_questions, question_in
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth as auth_module
from app.main import app
from app.models import User
from app.schemas import CardUpdate, DeckUpdate, QuestionUpdate
from app.services import content
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
    assert ">New</th>" in decks.text and ">2</td>" in decks.text

    detail = client.get(f"/app/decks/{parent.id}")
    assert detail.status_code == 200
    assert "Start session" in detail.text
    assert "S3" in detail.text
    assert "Performance" in detail.text

    child_detail = client.get(f"/app/decks/{child.id}")
    assert child_detail.status_code == 200
    assert f'href="/app/decks/{parent.id}"' in child_detail.text
    assert "AWS" in child_detail.text
    assert "Single" in child_detail.text
    assert "0/0" in child_detail.text
    assert "Never" in child_detail.text


def test_markdown_renders_on_question_detail(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "AWS")
    question = content.create_questions(db, user, deck.id, [question_in("stem")])[0]
    content.update_question(
        db,
        user,
        question.id,
        QuestionUpdate(stem="What is **S3**?\n\n- object storage"),
    )

    page = client.get(f"/app/questions/{question.id}")
    assert page.status_code == 200
    assert "<strong>S3</strong>" in page.text
    assert "<li>object storage</li>" in page.text

    content.update_question(db, user, question.id, QuestionUpdate(stem="What is **S3**?\n\n<script>alert(1)</script>"))
    xss = client.get(f"/app/questions/{question.id}")
    assert "<strong>S3</strong>" in xss.text
    assert "<script>alert(1)</script>" not in xss.text
    assert "&lt;script&gt;" in xss.text


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


def test_deck_questions_paginate(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "Big")
    make_questions(db, user, deck, n=12, prefix="Stem")

    first = client.get(f"/app/decks/{deck.id}").text
    assert "Page 1 of 2" in first and 'href="?page=2"' in first and "Previous" not in first
    assert "Stem10" not in first

    second = client.get(f"/app/decks/{deck.id}?page=2").text
    assert "Page 2 of 2" in second and 'href="?page=1"' in second and "Next" not in second
    assert "Stem10" in second and "Stem11" in second

    assert client.get(f"/app/decks/{deck.id}?page=0").status_code == 422


def test_deck_question_search(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "Big")
    make_questions(db, user, deck, n=11, prefix="Lambda")
    make_questions(db, user, deck, n=1, prefix="Glacier")

    found = client.get(f"/app/decks/{deck.id}", params={"q": "glacier0 option 3"}).text  # matches option text
    assert "Glacier0" in found and "Lambda0" not in found and 'value="glacier0 option 3"' in found

    paged = client.get(f"/app/decks/{deck.id}", params={"q": "lambda"}).text
    assert "Page 1 of 2" in paged and 'href="?q=lambda&amp;page=2"' in paged

    none = client.get(f"/app/decks/{deck.id}", params={"q": "nothing like this"}).text
    assert "No questions match" in none and 'name="q"' in none


def test_other_users_deck_is_404_html(client: TestClient, db: Session, user: User, other: User) -> None:
    login_web(user)
    deck = make_deck(db, other, "Secret")
    response = client.get(f"/app/decks/{deck.id}")
    assert response.status_code == 404
    assert "Not found" in response.text


def test_question_and_card_crud(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "AWS")

    form = client.get(f"/app/decks/{deck.id}/questions/new")
    assert form.status_code == 200
    assert "New question" in form.text

    created = client.post(
        f"/app/decks/{deck.id}/questions/new",
        data={
            "type": "single",
            "stem": "What is S3?",
            "explanation": "Object storage",
            "correct": "0",
            "option_text_0": "Object storage",
            "option_explanation_0": "Yes",
            "option_text_1": "A database",
            "option_explanation_1": "No",
            "option_text_2": "A CDN",
            "option_explanation_2": "No",
            "option_text_3": "A VPC",
            "option_explanation_3": "No",
        },
    )
    assert created.status_code == 303
    question_path = created.headers["location"]
    assert question_path.startswith("/app/questions/")
    question_id = int(question_path.rsplit("/", 1)[-1])

    detail = client.get(question_path)
    assert detail.status_code == 200
    assert "What is S3?" in detail.text
    assert "Object storage" in detail.text
    assert "Card" in detail.text

    deck_page = client.get(f"/app/decks/{deck.id}")
    assert "What is S3?" in deck_page.text
    assert f"/app/questions/{question_id}" in deck_page.text
    assert "Add question" in deck_page.text

    edited = client.post(
        f"/app/questions/{question_id}/edit",
        data={
            "type": "single",
            "deck_id": str(deck.id),
            "stem": "What is Amazon S3?",
            "explanation": "Object storage",
            "correct": "0",
            "option_id_0": "",
            "option_text_0": "Object storage",
            "option_text_1": "A database",
            "option_text_2": "A CDN",
            "option_text_3": "A VPC",
        },
    )
    assert edited.status_code == 303
    assert "What is Amazon S3?" in client.get(question_path).text

    card = client.post(
        f"/app/questions/{question_id}/card",
        data={"note": "Remember buckets", "suspended": "on"},
    )
    assert card.status_code == 303
    saved = client.get(question_path)
    assert "Remember buckets" in saved.text
    assert "suspended" in saved.text

    deleted = client.post(f"/app/questions/{question_id}/delete")
    assert deleted.status_code == 303
    assert deleted.headers["location"] == f"/app/decks/{deck.id}"
    assert client.get(question_path).status_code == 404


def test_archived_and_suspended_pages(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    parent = make_deck(db, user, "Parent")
    child = make_deck(db, user, "Child", parent_id=parent.id)
    content.update_deck(db, user, child.id, DeckUpdate(archived=True))
    active, paused = make_questions(db, user, parent, n=2, prefix="P")
    content.update_card(db, user, paused.id, CardUpdate(suspended=True))

    index = client.get("/app")
    assert index.status_code == 200
    assert "Archived" in index.text
    assert "Parent" in index.text
    assert "Child" not in index.text

    archived = client.get("/app/archived")
    assert archived.status_code == 200
    assert "Child" in archived.text

    parent_page = client.get(f"/app/decks/{parent.id}")
    assert parent_page.status_code == 200
    assert "Archived subdecks (1)" in parent_page.text
    assert "Suspended (1)" in parent_page.text
    assert "P0" in parent_page.text  # active stem prefix
    assert f"/app/questions/{paused.id}" not in parent_page.text

    kids = client.get(f"/app/decks/{parent.id}/archived")
    assert kids.status_code == 200
    assert "Child" in kids.text
    assert f'href="/app/decks/{child.id}"' in kids.text

    suspended = client.get(f"/app/decks/{parent.id}/suspended")
    assert suspended.status_code == 200
    assert f'href="/app/questions/{paused.id}"' in suspended.text
    assert f'href="/app/questions/{active.id}"' not in suspended.text

    # Deep links still work.
    assert client.get(f"/app/decks/{child.id}").status_code == 200
    assert "Archived" in client.get(f"/app/decks/{child.id}").text
    assert client.get(f"/app/questions/{paused.id}").status_code == 200


def test_deck_crud(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    assert db is not None  # rolled-back transaction fixture wraps the HTTP calls
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


def test_options_without_explanations_render(client: TestClient, db: Session, user: User) -> None:
    login_web(user)
    deck = make_deck(db, user, "Bare", session_size=1)
    bare = question_in("Bare").model_copy(update={"explanation": None})
    bare.options = [o.model_copy(update={"explanation": None}) for o in bare.options]
    (question,) = content.create_questions(db, user, deck.id, [bare])

    assert client.get(f"/app/questions/{question.id}/edit").status_code == 200
    session_path = client.post(f"/app/decks/{deck.id}/sessions").headers["location"]
    correct = [o["id"] for o in question.options if o["correct"]]
    result = client.post(
        f"{session_path}/answers", data={"question_id": str(question.id), "selected": correct, "confidence": "confident"}
    )
    assert result.status_code == 200

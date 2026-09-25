from collections.abc import Iterator

import pytest
from conftest import question_in
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth as auth_module, config
from app.auth import bearer_claims
from app.main import app
from app.models import Deck, User


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login_as(login: str = "alice", sub: str = "100", name: str = "Alice") -> None:
    app.dependency_overrides[bearer_claims] = lambda: {"sub": sub, "login": login, "name": name, "email": None}


def test_up_is_public(client: TestClient) -> None:
    assert client.get("/up").json() == {"status": "ok"}


def test_missing_token_is_401_with_resource_metadata(client: TestClient) -> None:
    response = client.get("/decks")
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_mocked_sign_in_needs_no_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "MOCK", True)
    assert client.get("/me").json()["login"] == "dev"


def test_no_github_creds_off_localhost_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_URL", "https://quiz.example.com")
    with pytest.raises(RuntimeError, match="GITHUB_CLIENT_ID"):
        auth_module.build_auth()


def test_no_github_creds_in_production_refuses_to_start_even_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_ENV", "production")
    with pytest.raises(RuntimeError, match="GITHUB_CLIENT_ID"):
        auth_module.build_auth()


def test_login_not_allowlisted_is_403(client: TestClient, db: Session) -> None:
    login_as("mallory")
    assert client.get("/me").status_code == 403
    assert db.query(User).count() == 0


def test_user_upserted_by_github_id(client: TestClient, db: Session) -> None:
    login_as(name="Alice")
    client.get("/me")
    login_as(login="ALICE", name="Alice B")  # case-insensitive allowlist, profile refreshed
    assert client.get("/me").json()["name"] == "Alice B"
    assert db.query(User).count() == 1


def test_settings_validated(client: TestClient) -> None:
    login_as()
    assert client.patch("/me", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.patch("/me", json={"timezone": "Europe/Paris"}).json()["timezone"] == "Europe/Paris"


def test_study_loop_over_rest(client: TestClient) -> None:
    login_as()
    deck = client.post("/decks", json={"name": "AWS"}).json()
    created = client.post(f"/decks/{deck['id']}/questions", json=[question_in("S3").model_dump()])
    assert created.status_code == 201
    session = client.post(f"/decks/{deck['id']}/sessions").json()
    question = client.get(f"/sessions/{session['session_id']}/next").json()["question"]

    correct = [o["id"] for o in created.json()[0]["options"] if o["correct"]]
    result = client.post(
        f"/sessions/{session['session_id']}/answers",
        json={"question_id": question["id"], "selected": correct, "confidence": "confident"},
    ).json()
    assert result["correct"] is True
    assert client.get(f"/sessions/{session['session_id']}/next").json()["finished"] is True


def test_invalid_batch_creates_nothing(client: TestClient) -> None:
    login_as()
    deck = client.post("/decks", json={"name": "AWS"}).json()
    bad = question_in("bad").model_dump()
    bad["options"] = bad["options"][:3]
    response = client.post(f"/decks/{deck['id']}/questions", json=[question_in("ok").model_dump(), bad])
    assert response.status_code == 422
    assert client.get(f"/decks/{deck['id']}").json()["questions"] == []


def test_other_users_deck_is_404(client: TestClient) -> None:
    login_as("bob", sub="200")
    deck = client.post("/decks", json={"name": "Bob's"}).json()
    login_as()
    assert client.get(f"/decks/{deck['id']}").status_code == 404


def test_delete_me_cascades(client: TestClient, db: Session) -> None:
    login_as()
    client.post("/decks", json={"name": "AWS"})
    assert client.delete("/me").status_code == 204
    assert db.query(User).count() == 0 and db.query(Deck).count() == 0

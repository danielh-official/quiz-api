import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def test_home_is_static(client: TestClient) -> None:
    page = client.get("/").text
    assert f"{config.APP_URL}/mcp" in page and 'href="/docs"' in page and "{{" not in page and "{%" not in page
    assert "claude plugin install" not in page  # no marketplace configured
    assert "<form" not in page and "Sign in" not in page and 'type="password"' not in page  # nothing to log in to
    assert "noindex" in page and "fetch(" not in page


def test_home_shows_plugin_when_marketplace_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PLUGIN_MARKETPLACE", "someone/<quiz-api>")
    page = client.get("/").text
    assert "claude plugin marketplace add someone/&lt;quiz-api&gt;" in page
    assert "claude mcp add" in page  # fallback stays


def test_sign_in_pages_are_gone(client: TestClient) -> None:
    assert client.post("/login").status_code in (404, 405) and client.get("/token").status_code != 200


def test_robots_disallows_everything(client: TestClient) -> None:
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from mcp.shared.auth import OAuthToken
from pydantic import AnyUrl

from app import auth as auth_module, config, web
from app.auth import WEB_REDIRECT_URI
from app.main import app

ALICE = {"sub": "100", "login": "alice", "name": "Alice", "email": None}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def signed_in(client: TestClient, claims: dict[str, Any] | None = None) -> None:
    app.dependency_overrides[web.session_claims] = lambda: claims or ALICE
    client.cookies.set(web.SESSION_COOKIE, "the-token")


class FakeProvider:
    """Just enough of FastMCP's OAuth provider for the web sign-in flow; the PKCE check in exchange_code is real."""

    def __init__(self) -> None:
        self.challenge = ""  # what /authorize would have stored with the code

    async def get_client(self, client_id: str) -> SimpleNamespace:
        return SimpleNamespace(client_id=client_id)

    async def load_authorization_code(self, _client: Any, code: str) -> SimpleNamespace | None:
        return SimpleNamespace(code_challenge=self.challenge, redirect_uri=AnyUrl(WEB_REDIRECT_URI)) if code == "good" else None

    async def exchange_authorization_code(self, _client: Any, _code: Any) -> OAuthToken:
        return OAuthToken(access_token="issued-token", token_type="Bearer", expires_in=3600)

    async def load_access_token(self, token: str) -> SimpleNamespace | None:
        return SimpleNamespace(claims=ALICE) if token == "issued-token" else None


def test_home_signed_out(client: TestClient) -> None:
    page = client.get("/").text
    assert 'action="/login"' in page and "Sign out" not in page and "Bearer" not in page and 'role="alert"' not in page
    assert f"{config.APP_URL}/mcp" in page and "{{" not in page and "<!--if:" not in page
    assert "claude plugin install" not in page  # no marketplace configured
    assert "sessionStorage" not in page and "fetch(" not in page  # no client-side rendering


def test_home_shows_plugin_when_marketplace_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PLUGIN_MARKETPLACE", "someone/<quiz-api>")
    page = client.get("/").text
    assert "claude plugin marketplace add someone/&lt;quiz-api&gt;" in page
    assert "claude mcp add" in page  # fallback stays


def test_home_signed_in(client: TestClient) -> None:
    signed_in(client)
    response = client.get("/")
    assert "@alice" in response.text and 'action="/logout"' in response.text
    assert "Bearer the-token" in response.text and 'action="/login"' not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_home_drops_session_no_longer_allowlisted(client: TestClient) -> None:
    signed_in(client, {**ALICE, "login": "mallory"})
    response = client.get("/")
    assert 'action="/login"' in response.text and "the-token" not in response.text
    assert f'{web.SESSION_COOKIE}=""' in response.headers["set-cookie"]


def test_login_redirects_home_when_signed_in(client: TestClient) -> None:
    signed_in(client)
    for response in (client.get("/login"), client.post("/login")):
        assert response.status_code == 303 and response.headers["location"] == "/"


def test_login_without_callback_redirects_home(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_login_without_github_configured(client: TestClient) -> None:
    response = client.post("/login")
    assert response.status_code == 503 and "configured on this server" in response.text
    assert 'action="/login"' in response.text  # shown on the home page


def test_sign_in_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(auth_module, "auth", provider)

    start = client.post("/login")
    assert start.status_code == 303
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    assert query["client_id"] == "web" and query["redirect_uri"] == WEB_REDIRECT_URI
    assert query["code_challenge_method"] == "S256"

    assert "tampered" in client.get("/login", params={"code": "good", "state": "forged"}).text
    start = client.post("/login")  # the failed callback cleared the flow; start over
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}

    provider.challenge = "not-" + query["code_challenge"]  # a code issued for someone else's verifier
    assert "sign-in failed" in client.get("/login", params={"code": "good", "state": query["state"]}).text

    start = client.post("/login")
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    provider.challenge = query["code_challenge"]
    done = client.get("/login", params={"code": "good", "state": query["state"]})
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert "HttpOnly" in done.headers["set-cookie"] and client.cookies[web.SESSION_COOKIE] == "issued-token"
    assert "@alice" in client.get("/").text
    assert client.get("/login").status_code == 303


def test_sign_in_error_from_github(client: TestClient) -> None:
    response = client.get("/login", params={"error": "access_denied", "error_description": "<b>denied</b>"})
    assert response.status_code == 400 and "&lt;b&gt;denied&lt;/b&gt;" in response.text


def test_logout_clears_session(client: TestClient) -> None:
    signed_in(client)
    response = client.post("/logout")
    assert response.status_code == 303 and f'{web.SESSION_COOKIE}=""' in response.headers["set-cookie"]

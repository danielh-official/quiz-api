import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import question_in
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from key_value.aio.stores.memory import MemoryStore
from mcp.shared.auth import OAuthClientInformationFull
from mcp.types import Tool

from app import auth as auth_module, config, mcp as mcp_module

TOOLS = {
    "list-decks", "get-deck", "search-questions", "get-performance", "create-deck", "update-deck",
    "create-questions", "update-question", "start-session", "next-question", "submit-answer",
    "update-session", "update-card", "update-settings",
}  # fmt: skip


def run[T](monkeypatch: pytest.MonkeyPatch, steps: Callable[[Client[Any]], Awaitable[T]], login: str = "alice") -> T:
    """Run async steps against the MCP server in memory, as a signed-in GitHub user."""
    claims = {"sub": "100", "login": login, "name": login, "email": None}
    monkeypatch.setattr(
        mcp_module, "get_access_token", lambda: SimpleNamespace(claims=claims)
    )

    async def main() -> T:
        async with Client(mcp_module.mcp) as client:
            return await steps(client)

    return asyncio.run(main())


def test_tools_and_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps(client: Client[Any]) -> list[Tool]:
        return await client.list_tools()

    tools = {t.name: t for t in run(monkeypatch, steps)}
    assert set(tools) == TOOLS
    annotations = tools["list-decks"].annotations
    assert annotations is not None and annotations.read_only_hint is True


def test_full_session_without_answer_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps(
        client: Client[Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
        deck = (await client.call_tool("create-deck", {"name": "AWS"})).data["deck"]
        await client.call_tool(
            "create-questions",
            {
                "deck_id": deck["id"],
                "questions": [question_in("S3").model_dump(exclude_none=True)],
            },
        )
        session = (
            await client.call_tool("start-session", {"deck_id": deck["id"]})
        ).data
        nxt = (
            await client.call_tool(
                "next-question", {"session_id": session["session_id"]}
            )
        ).data
        leaked = [o for o in nxt["question"]["options"] if set(o) != {"id", "text"}]
        result = (
            await client.call_tool(
                "submit-answer",
                {
                    "session_id": session["session_id"],
                    "question_id": nxt["question"]["id"],
                    "selected": [nxt["question"]["options"][0]["id"]],
                    "confidence": "educated_guess",
                },
            )
        ).data
        done = (
            await client.call_tool(
                "next-question", {"session_id": session["session_id"]}
            )
        ).data
        return leaked, nxt, result, done

    leaked, nxt, result, done = run(monkeypatch, steps)
    assert leaked == [] and "explanation" not in nxt["question"]
    assert "explanation" in result["question"]
    assert done["finished"] and done["summary"]["answered"] == 1


def test_session_summary_carries_to_next_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps(client: Client[Any]) -> list[dict[str, Any]]:
        deck = (await client.call_tool("create-deck", {"name": "AWS"})).data["deck"]
        first = (await client.call_tool("start-session", {"deck_id": deck["id"]})).data
        await client.call_tool("update-session", {"session_id": first["session_id"], "summary": "Weak on RDS."})
        return list((await client.call_tool("start-session", {"deck_id": deck["id"]})).data["recent_summaries"])

    [recent] = run(monkeypatch, steps)
    assert recent["summary"] == "Weak on RDS." and recent["deck"] == "AWS"


def test_errors_are_tool_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps(client: Client[Any]) -> str | None:
        try:
            await client.call_tool("get-deck", {"deck_id": 999999})
        except ToolError as exc:
            return str(exc)
        return None

    result = run(monkeypatch, steps)

    if result is None:
        pytest.fail("Expected a ToolError but none was raised")

    assert "not found" in result


def test_mocked_sign_in_needs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "MOCK", True)
    monkeypatch.setattr(mcp_module, "get_access_token", lambda: None)

    async def main() -> Any:
        async with Client(mcp_module.mcp) as client:
            return (await client.call_tool("list-decks", {})).data

    assert asyncio.run(main()) == {"decks": []}


def test_not_allowlisted_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def steps(client: Client[Any]) -> str | None:
        try:
            await client.call_tool("list-decks", {})
        except ToolError as exc:
            return str(exc)
        return None

    result = run(monkeypatch, steps, login="mallory")
    if result is None:
        pytest.fail("Expected a ToolError but none was raised")
    assert "not allowed" in result


def test_oauth_metadata_served_when_github_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GITHUB_CLIENT_ID": "id",
        "GITHUB_CLIENT_SECRET": "secret",
        "JWT_SIGNING_KEY": "k" * 32,
        "STORAGE_ENCRYPTION_KEY": "e" * 32,
    }.items():
        monkeypatch.setattr(config, name, value)
    app = FastMCP("t", auth=auth_module.build_auth()).http_app(path="/mcp")

    with TestClient(app) as client:
        resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
        server = client.get("/.well-known/oauth-authorization-server").json()
        assert client.post("/mcp", json={}).status_code == 401
    assert resource["resource"] == f"{config.APP_URL}/mcp"
    assert server["registration_endpoint"].endswith("/register")


def test_registration_rejects_foreign_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamic registration only admits known client callbacks, so /authorize can't redirect to arbitrary sites."""
    for name, value in {
        "GITHUB_CLIENT_ID": "id",
        "GITHUB_CLIENT_SECRET": "secret",
        "JWT_SIGNING_KEY": "k" * 32,
        "STORAGE_ENCRYPTION_KEY": "e" * 32,
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(auth_module, "PostgreSQLStore", lambda url: MemoryStore())
    app = FastMCP("t", auth=auth_module.build_auth()).http_app(path="/mcp")

    def register(uri: str) -> int:
        body = {"client_name": "c", "redirect_uris": [uri], "token_endpoint_auth_method": "none"}
        return client.post("/register", json=body).status_code

    with TestClient(app) as client:
        assert register("http://localhost:53123/callback") == 201
        assert register("https://claude.ai/api/mcp/auth_callback") == 201
        assert register("https://evil.example/callback") == 400
        assert register("https://claude.ai.evil.example/callback") == 400


def test_swagger_client_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GITHUB_CLIENT_ID": "id",
        "GITHUB_CLIENT_SECRET": "secret",
        "JWT_SIGNING_KEY": "k" * 32,
        "STORAGE_ENCRYPTION_KEY": "e" * 32,
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(auth_module, "PostgreSQLStore", lambda url: MemoryStore())
    provider = auth_module.build_auth()
    if provider is None:
        pytest.fail("Expected provider to be initialized")
    monkeypatch.setattr(auth_module, "auth", provider)

    async def main() -> OAuthClientInformationFull | None:
        await auth_module.register_swagger_client()
        return await provider.get_client("swagger-ui")

    swagger = asyncio.run(main())
    if swagger is None or swagger.redirect_uris is None:
        pytest.fail("Expected the swagger client to be registered with redirect URIs")
    assert [str(u) for u in swagger.redirect_uris] == [f"{config.APP_URL}/docs/oauth2-redirect"]

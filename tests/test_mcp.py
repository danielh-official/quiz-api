import asyncio
from types import SimpleNamespace

from conftest import question_in
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from app import auth as auth_module, config, mcp as mcp_module

TOOLS = {
    "list-decks", "get-deck", "search-questions", "get-performance", "create-deck", "update-deck",
    "create-questions", "update-question", "start-session", "next-question", "submit-answer",
    "update-card", "update-settings",
}  # fmt: skip


def run(monkeypatch, steps, login="alice"):
    """Run async steps against the MCP server in memory, as a signed-in GitHub user."""
    claims = {"sub": "100", "login": login, "name": login, "email": None}
    monkeypatch.setattr(mcp_module, "get_access_token", lambda: SimpleNamespace(claims=claims))

    async def main():
        async with Client(mcp_module.mcp) as client:
            return await steps(client)

    return asyncio.run(main())


def test_tools_and_annotations(monkeypatch):
    async def steps(client):
        return await client.list_tools()

    tools = {t.name: t for t in run(monkeypatch, steps)}
    assert set(tools) == TOOLS
    assert tools["list-decks"].annotations.read_only_hint is True


def test_full_session_without_answer_leakage(monkeypatch):
    async def steps(client):
        deck = (await client.call_tool("create-deck", {"name": "AWS"})).data["deck"]
        await client.call_tool(
            "create-questions", {"deck_id": deck["id"], "questions": [question_in("S3").model_dump(exclude_none=True)]}
        )
        session = (await client.call_tool("start-session", {"deck_id": deck["id"]})).data
        nxt = (await client.call_tool("next-question", {"session_id": session["session_id"]})).data
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
        done = (await client.call_tool("next-question", {"session_id": session["session_id"]})).data
        return leaked, nxt, result, done

    leaked, nxt, result, done = run(monkeypatch, steps)
    assert leaked == [] and "explanation" not in nxt["question"]
    assert "explanation" in result["question"]
    assert done["finished"] and done["summary"]["answered"] == 1


def test_errors_are_tool_errors(monkeypatch):
    async def steps(client):
        try:
            await client.call_tool("get-deck", {"deck_id": 999999})
        except ToolError as exc:
            return str(exc)

    assert "not found" in run(monkeypatch, steps)


def test_not_allowlisted_is_rejected(monkeypatch):
    async def steps(client):
        try:
            await client.call_tool("list-decks", {})
        except ToolError as exc:
            return str(exc)

    assert "not allowed" in run(monkeypatch, steps, login="mallory")


def test_oauth_metadata_served_when_github_configured(monkeypatch):
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

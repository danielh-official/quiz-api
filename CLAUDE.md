# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Spaced-repetition multiple-choice quiz app: FastAPI REST API + FastMCP server at `/mcp`, PostgreSQL, GitHub OAuth.
README.md covers setup, env vars, the REST route table and how to connect AI clients.

## Commands

```bash
docker compose up -d --build                      # API on :8000, Postgres on :5433 (dbs `quiz` and `test`)
uv sync && uv run pytest                          # tests need compose's Postgres running
uv run pytest tests/test_limits.py -k parent      # one file / matching tests
uv run --with mypy mypy app tests                 # strict mode, config in pyproject.toml
uv run --with pylint pylint app tests migrations  # max line length 130
uv run alembic revision --autogenerate -m "..."   # after changing app/models.py
```

mypy and pylint aren't dev dependencies; run them with `--with` as above. Migrations run on container start.

## Architecture

- **`app/services/`** (`content`, `study`, `stats`) holds all business logic. Functions take `(db, user, ...)` and
  raise `NotFound` / `Invalid` / `Forbidden` from `app/services/__init__.py`. `NotFound` also covers "not yours":
  keep missing and unauthorized indistinguishable.
- **`app/api.py`** (REST) and **`app/mcp.py`** (MCP tools) are thin wrappers over the services. `app/main.py` maps
  service errors to 404/422/403; `mcp.caller()` turns them into `ToolError`. New features go in a service first,
  then get exposed in both. MCP has no delete tools on purpose: deletes are REST-only.
- **`app/mcp.py` `INSTRUCTIONS`** is the guide MCP clients read. Keep it in sync with
  `plugins/quiz-api/skills/quiz-api/SKILL.md` when behavior changes. MCP tool signatures are the tool schema, which
  is why pylint allows 8 args.
- **`app/main.py`** mounts the FastMCP app at `/` last. It serves `/mcp` and all OAuth endpoints (`/authorize`,
  `/token`, `/register`, `/auth/callback`, `/.well-known/*`), so REST routes must be registered before it.
- **Auth (`app/auth.py`)**: the server is its own OAuth 2.1 server via FastMCP's `GitHubProvider`, with state encrypted
  in a `kv_store` table (auto-created, not in Alembic). One bearer token works for REST and MCP. `resolve_user`
  checks `ALLOWED_USERS`, then upserts the user keyed by GitHub's numeric `sub`, not the login. Without
  `GITHUB_CLIENT_ID`/`SECRET` on a localhost `APP_URL`, `auth` is `None` and `MOCK` is on: `token_claims` and
  `mcp.caller()` return `MOCK_CLAIMS` (user `dev`, allowlist skipped). Without them anywhere else, or with
  `APP_ENV=production` (set in the Dockerfile's prod stage), `build_auth` raises at startup. Dynamic client
  registration only accepts `CLIENT_REDIRECT_URIS` (loopback, claude.ai/.com, chatgpt.com, `APP_URL`). Render
  suspended the deploy for "suspicious activity"; open redirects and login-first pages read as phishing, so keep
  both out (README "Avoiding suspension"). Tests turn `MOCK` off in conftest; mock tests turn it back on.
- **Web (`app/web.py`, `app/templates/`)**: server-rendered Jinja pages. `/login` runs the PKCE flow server-side as
  the pre-registered `web` client and stores the token in the HttpOnly `quiz_session` cookie.
- **Study (`app/services/study.py`)**: FSRS with no learning steps, so every interval is whole days. The rating
  comes from correctness + confidence (`rating_for`). The study day starts at 4am in the user's timezone. Daily
  new-question limits follow Anki v3 rules down the nested deck tree (`new_remaining`, `admits`).
- **Decks nest**: study, search and stats on a deck include its subtree (`content.subtree_ids`). `content`,
  `study` and `stats` import each other, and the back-edges are lazy imports (pylint `cyclic-import` is disabled).
- **Questions** store options as JSON with stable ids. `single` = 4 options, 1 correct; `select_two` = 5 options,
  2 correct (`schemas.QUESTION_SHAPES`). On update, passing an option's `id` back keeps it.

## Tests

- `tests/conftest.py` points `DATABASE_URL` at the `test` db (`TEST_DATABASE_URL` overrides), downgrades and
  re-upgrades Alembic once per run, and wraps each test in a rolled-back transaction (commits become savepoints).
  It also patches `SessionLocal` in both `app.db` and `app.mcp`, turns off `study.FUZZ`, and sets
  `ALLOWED_USERS={"alice","bob"}`.
- GitHub creds are unset in tests. MCP tests monkeypatch `mcp.get_access_token` and use an in-memory
  `fastmcp.Client` (`run()` in `tests/test_mcp.py`).
- Use the helpers in conftest (`make_user`, `make_deck`, `make_questions`, `question_in`, `answer`), imported as
  `from conftest import ...`.

## Conventions

- `ponytail:` comments mark deliberate simplifications and name the upgrade path. README's "Deliberately left out"
  lists features skipped on purpose (rate limiting, scopes, PATs, multi-instance); don't add them unasked.
- Changing the plugin's MCP URL: edit `plugins/quiz-api/.mcp.json`, bump `version` in `plugin.json`, then run
  `claude plugin validate plugins/quiz-api`.

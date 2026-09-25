# Quiz API

Spaced-repetition multiple-choice quizzes as a small FastAPI + PostgreSQL REST API, with an MCP server at `/mcp`
so Claude, ChatGPT and other MCP clients can quiz you and write questions. Sign-in is GitHub OAuth, and only
GitHub logins listed in `ALLOWED_USERS` can use it. This is a trimmed-down rewrite of master-quiz (Laravel).

- **Decks** nest. **Questions** are `single` (4 options, 1 correct) or `select_two` (5 options, 2 correct).
- Scheduling uses [FSRS](https://github.com/open-spaced-repetition/py-fsrs) with whole-day intervals. The
  confidence you give with each answer sets the rating.
- Each deck has a daily new-question limit that follows Anki v3 rules. The study day starts at 4am in your timezone.

## Run it

```bash
cp .env.example .env
docker compose up -d --build
curl localhost:8000/up
```

Postgres listens on `localhost:5433`, with two databases: `quiz` and `test`. Migrations run when the API
container starts.

- http://localhost:8000/ is the home page: sign in with GitHub (which creates your account), how to connect an AI
  and, once you're signed in, your access token for the REST API.
- http://localhost:8000/docs has the OpenAPI docs. Click **Authorize** to sign in with GitHub and try requests.

### GitHub sign-in

1. Create an OAuth app at https://github.com/settings/developers:
   - Homepage: `APP_URL`
   - Callback: `<APP_URL>/auth/callback`
2. Fill in these values in `.env`:
   - `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`
   - `JWT_SIGNING_KEY` and `STORAGE_ENCRYPTION_KEY`: random strings (see the comments in `.env.example`)
   - `ALLOWED_USERS`: your GitHub login
3. Restart with `docker compose up -d`.

The API is its own OAuth 2.1 authorization server, built on FastMCP's `GitHubProvider`. It supports dynamic
client registration, PKCE and a consent screen. OAuth clients and tokens live in the `kv_store` table,
encrypted. The same bearer tokens work for `/mcp` and for the REST API. The web pages (client `web`) and
`/docs` (client `swagger-ui`) are public PKCE clients that register themselves when the server starts.

The pages are rendered on the server. The home page's sign-in button posts to `/login`, which runs the PKCE flow
server-side (the OAuth redirect lands back on `/login`) and keeps the access token in an HttpOnly cookie
(`quiz_session`) that lasts as long as the token. There's no refresh, so you sign in again when it expires. The only
JavaScript left is the Copy buttons.

## Connect an AI

- **Claude Code**: install the plugin (below), or run
  `claude mcp add --transport http quiz-api http://localhost:8000/mcp`. Then run `/mcp` to sign in.
- **Claude.ai / Claude Desktop**: Customize → Connectors → "+" → Add custom connector → `<APP_URL>/mcp`. The
  connection comes from Anthropic's servers, so `APP_URL` must be publicly reachable (not localhost).
- **ChatGPT** (Plus, Pro, Business, Enterprise, Edu; web): Settings → Security and login → Developer mode, then
  ChatGPT Plugins → "+" → create a developer-mode app with `<APP_URL>/mcp` and OAuth.

The home page shows the plugin install commands when `PLUGIN_MARKETPLACE` is set (e.g. the repo path, or
`owner/repo` once published); otherwise it shows only `claude mcp add`.

### Claude Code plugin

`plugins/quiz-api/` bundles the MCP server config (`.mcp.json`) and the `quiz-api` skill, which covers the study
loop, writing good questions and reviewing progress. `.claude-plugin/marketplace.json` makes this repo a plugin
marketplace.

```bash
claude plugin marketplace add .   # from the repo root; or owner/repo once it's on GitHub
claude plugin install quiz-api@quiz-api
```

The plugin points at `http://localhost:8000/mcp`. Keep it in sync with `APP_URL`: change the URL in
`plugins/quiz-api/.mcp.json` once the server is deployed, and bump `version` in `plugin.json` so installs pick it up. Validate with
`claude plugin validate plugins/quiz-api`.

For Claude.ai or ChatGPT, zip the skill folder (`cd plugins/quiz-api/skills && zip -r quiz-api.zip quiz-api`)
and upload it under Skills.

## REST API

Every route needs `Authorization: Bearer <token>`, except `/up`, the web pages (`/`, `/login`, `/logout`), `/docs` and the OAuth endpoints.

| Method | Path | |
|---|---|---|
| GET, PATCH, DELETE | `/me` | Profile and settings (`timezone`, `desired_retention`). DELETE removes everything |
| GET, POST | `/decks` | Tree with due/new/total counts; create |
| GET, PATCH, DELETE | `/decks/{id}` | GET adds subdecks and questions (`?page=`) |
| POST | `/decks/{id}/questions` | 1–50 questions, all-or-nothing |
| GET | `/questions/search?q=&deck_id=` | |
| GET, PATCH, DELETE | `/questions/{id}` | PATCH: pass option `id`s back to keep them; `reset_progress` |
| PUT | `/questions/{id}/card` | `note`, `suspended` |
| POST | `/decks/{id}/sessions` | Start a session, optional `size` |
| GET | `/sessions/{id}/next` | Next question without answers, or the summary when done |
| POST | `/sessions/{id}/answers` | `question_id`, `selected` (option ids), `confidence` |
| GET | `/stats?deck_id=` | Calibration, misconceptions, leeches, notes |

MCP tools: list-decks, get-deck, search-questions, get-performance, create-deck, update-deck,
create-questions, update-question, start-session, next-question, submit-answer, update-card and
update-settings. There are no delete tools; deleting goes through REST only.

## Develop

```bash
docker compose exec api pytest                    # inside the container
uv sync && uv run pytest                          # on the host (uses localhost:5433/test)
uv run pytest tests/test_limits.py -k parent      # one file / matching tests
uv run alembic revision --autogenerate -m "..."   # after changing app/models.py
```

Tests reset the `test` database (Alembic downgrade to base, then upgrade) at the start of every run, and
roll back each test's transaction. Compose must be up for Postgres.

Layout:

- `app/services/`: all the business logic
- `app/api.py` and `app/mcp.py`: thin wrappers over the services
- `app/auth.py`: OAuth and user mapping
- `app/web.py` and `app/templates/`: the server-rendered pages

## Deliberately left out

These can be added when needed:

- **Rate limiting**: add it when signup opens beyond the allowlist.
- **OAuth scopes**: add them for third-party clients.
- **Personal access tokens**: add them for scripts.
- **Google sign-in**: needs a custom FastMCP `OAuthProvider`.
- **Deck sharing**: not ported from master-quiz.
- **Running more than one instance**: tokens and OAuth state already live in Postgres, so only the migrations
  need to move out of the container start command (they run on every start).

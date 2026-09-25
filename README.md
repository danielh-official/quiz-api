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

Locally, sign-in is mocked: with no GitHub credentials in `.env` and `APP_URL` on localhost, every request (REST
and MCP) is the user `dev`, with no token or GitHub round trip. Without GitHub credentials, the server
refuses to start on any other `APP_URL`, and always in the production image (`APP_ENV=production` in the Dockerfile),
so production can't end up open by accident.

- http://localhost:8000/ is the home page: how to connect an AI and run your own copy.
- http://localhost:8000/docs has the OpenAPI docs, where *Authorize* signs you in to try the REST API.

### GitHub sign-in (production)

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
encrypted. The same bearer tokens work for `/mcp` and for the REST API. `/docs` (client
`swagger-ui`) is a public PKCE client that registers itself when the server starts.

The home page is static: it has no sign-in, because MCP clients and `/docs` run their own OAuth flows. The only
JavaScript is the Copy buttons.

## Deploy

Any host that runs a Dockerfile works, including AWS Lambda: the last stage (`prod`) is the production image, listens on `$PORT` (default
8080), runs migrations on start and serves a health check at `/up`. Keep Postgres off the app host (e.g. a free
[Neon](https://neon.tech) project, in the same region): if the host suspends or deletes the app, your data survives
and you redeploy elsewhere. For Neon, use the direct (non-pooled) URL without `&channel_binding=require`, which
asyncpg rejects.

Every host needs the same settings: `APP_URL` (the public URL, no trailing slash), `DATABASE_URL`,
`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ALLOWED_USERS`, and optionally `PLUGIN_MARKETPLACE`. Generate
`JWT_SIGNING_KEY` and `STORAGE_ENCRYPTION_KEY` with `python -c "import secrets; print(secrets.token_urlsafe(32))"`
(Render generates them for you). Then point the GitHub OAuth app's homepage and callback at the new URL.

Moving hosts: set the new `APP_URL`, update the GitHub OAuth app, and update the plugin URL (see
[Claude Code plugin](#claude-code-plugin)). Reusing the old keys and database keeps registered clients working; new
keys mean every client signs in again.

### Render

New → Blueprint, pick the repo: `render.yaml` sets up a free Docker web service in Virginia with the health check,
generates both keys and prompts for the rest. The free plan sleeps when idle, so the first request after a while
takes up to a minute.

### Railway

```bash
railway init                     # new project, from the repo root
railway up                       # builds the Dockerfile's prod stage
railway variables --set "DATABASE_URL=..." --set "GITHUB_CLIENT_ID=..."   # and the rest
railway domain                   # public URL; set APP_URL to it, then redeploy
```

In the service settings, set the healthcheck path to `/up` and the region next to your database. Railway sets
`PORT` itself.

### Fly

```bash
fly launch --no-deploy           # detects the Dockerfile, writes fly.toml; pick region iad for Neon us-east-1
fly secrets set DATABASE_URL=... GITHUB_CLIENT_ID=...   # and the rest; APP_URL=https://<app>.fly.dev
fly deploy
```

Check that `fly.toml` has `internal_port = 8080`, and add an `[[http_service.checks]]` with `path = "/up"`. With
`auto_stop_machines` on, idle machines stop and the dashboard shows the app as *suspended*: that's scale-to-zero,
not an account action.

### AWS Lambda

`infra/main.tf` (Terraform) runs the same image on Lambda, with [Lambda Web
Adapter](https://github.com/awslabs/aws-lambda-web-adapter) turning invocations into HTTP requests, behind an HTTP
API on your own domain. It also manages the domain's two Cloudflare DNS records (certificate validation and the
CNAME), so the domain's parent must be a Cloudflare zone. Everything lives in us-east-1, next to a Neon us-east-1
database.

Cost for a personal instance is close to $0: Lambda's always-free allowance (1M requests and 400,000 GB-seconds a
month) covers it, the HTTP API is $1 per million requests, and ECR keeps only the last 3 images (about $0.10 per
GB-month). AWS has no hard spending cap; set `BUDGET_EMAIL` to get an email when a month passes $1 or is forecast
to pass $5.

1. Install the AWS CLI, Terraform and Docker, and sign in with `aws login`.
2. Create a Cloudflare API token with **Zone → DNS → Edit** on your zone only.
3. Create `.env.aws` (gitignored) with `DOMAIN_NAME` (e.g. `quiz-api.example.com`), `DATABASE_URL`,
   `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `JWT_SIGNING_KEY`, `STORAGE_ENCRYPTION_KEY`, `ALLOWED_USERS`,
   `CLOUDFLARE_API_TOKEN`, and optionally `PLUGIN_MARKETPLACE` and `BUDGET_EMAIL`.
4. Run `deploy/aws.sh`. The first run asks to create the ECR repository, then builds and pushes the image, then
   shows the full plan and asks again. Creating the certificate waits a few minutes for validation.
5. Point the GitHub OAuth app at `https://<DOMAIN_NAME>` and `https://<DOMAIN_NAME>/auth/callback`.

Redeploy with `deploy/aws.sh`: each run pushes the current code and shows what changes before applying. The API only
answers on your domain (the default `execute-api` URL is off). The first request after a quiet spell cold-starts the
function, which takes a few seconds.

Terraform state stays local in `infra/terraform.tfstate`: gitignored, readable only by you, and holding the secrets
in plain text. Don't commit it or share it; lose it and Terraform no longer knows what it created (import or delete
the resources by hand).

### Avoiding suspension

Hosts scan for phishing and suspend first, and ask later. A small app on a shared domain (`*.onrender.com`,
`*.up.railway.app`, `*.fly.dev`) whose page leads with "Sign in with GitHub" looks a lot like the credential
phishing those domains are known for. This has happened to this project on Render. What keeps the risk down:

- **Use your own domain.** All three hosts support custom domains. Scanners and
  blocklists treat shared host subdomains with suspicion, and one bad neighbour can get the whole suffix flagged.
- **No login page.** The home page is static: it explains the project, calls itself a personal instance and has
  no sign-in, form or password field. Sign-in only happens inside MCP clients and behind Swagger's *Authorize*
  button on `/docs`, which nothing links to as a call to action.
- **No open redirects.** Dynamic client registration only accepts callbacks on loopback, `claude.ai`, `claude.com`,
  `chatgpt.com` and `/docs` (`CLIENT_REDIRECT_URIS` in `app/auth.py`). Without that list, anyone could register a
  client and hand out `/authorize` links on your domain that end on their own site. To support another MCP client,
  add its callback there.
- **Stay out of search.** `robots.txt` disallows everything and every page is `noindex`.
- **One account per person.** Railway's [fair use policy](https://railway.com/legal/fair-use) bans multiple
  trial accounts, and all three hosts' acceptable use policies let them suspend without notice
  ([Render](https://render.com/security), [Railway](https://railway.com/legal/acceptable-use),
  [Fly](https://fly.io/legal/acceptable-use-policy/)).

If you're suspended anyway: check the domain in [Google Safe
Browsing](https://transparencyreport.google.com/safe-browsing/search) and request a review there if it's listed,
then appeal to the host (Render through the dashboard's support, Railway at support@railway.app, Fly through
support or its community forum). Point to the public repo and explain that sign-in is limited to `ALLOWED_USERS`.
Meanwhile, deploy to another host: the database lives elsewhere, so nothing is lost.

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

The plugin points at the deployed server, `https://quiz-api.danielhaven.com/mcp`. Keep it in sync with `APP_URL`:
change the URL in `plugins/quiz-api/.mcp.json` and bump `version` in `plugin.json` so installs pick it up. Validate with
`claude plugin validate plugins/quiz-api`.

The URL is `${QUIZ_API_URL:-<production>}`, so setting `QUIZ_API_URL` points the same plugin at another server. The
repo's `.envrc` sets it to `http://localhost:8000/mcp`: with [direnv](https://direnv.net) (`direnv allow` once),
`claude` run inside this repo talks to your local, mocked server and everywhere else to production.

For Claude.ai or ChatGPT, zip the skill folder (`cd plugins/quiz-api/skills && zip -r quiz-api.zip quiz-api`)
and upload it under Skills.

## REST API

Every route needs `Authorization: Bearer <token>`, except `/up`, `/`, `/robots.txt`, `/docs` and the OAuth endpoints.

| Method | Path | |
|---|---|---|
| GET, PATCH, DELETE | `/me` | Profile and settings (`timezone`, `desired_retention`). DELETE removes everything |
| GET, POST | `/decks` | Tree with due/new/total counts; create |
| GET, PATCH, DELETE | `/decks/{id}` | GET adds subdecks and questions (`?page=`) |
| POST | `/decks/{id}/questions` | 1–50 questions, all-or-nothing |
| GET | `/questions/search?q=&deck_id=` | |
| GET, PATCH, DELETE | `/questions/{id}` | PATCH: pass option `id`s back to keep them; `reset_progress` |
| PUT | `/questions/{id}/card` | `note`, `suspended` |
| POST | `/decks/{id}/sessions` | Start a session, optional `size`; returns the latest session summaries |
| GET | `/sessions/{id}/next` | Next question without answers, or the summary when done |
| POST | `/sessions/{id}/answers` | `question_id`, `selected` (option ids), `confidence` |
| PATCH | `/sessions/{id}` | `summary`: the running free-form summary, replaced on each call |
| GET | `/stats?deck_id=&exam_date=` | Calibration, misconceptions, leeches, notes, readiness |

MCP tools: list-decks, get-deck, search-questions, get-performance, create-deck, update-deck,
create-questions, update-question, start-session, next-question, submit-answer, update-session,
update-card and update-settings. There are no delete tools; deleting goes through REST only.

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

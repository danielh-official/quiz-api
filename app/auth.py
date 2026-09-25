"""GitHub sign-in via FastMCP's OAuth proxy, and mapping token claims to users rows."""

import warnings
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2AuthorizationCodeBearer
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User
from app.services import Forbidden


# Where OAuth clients may send users back to. Without this list, dynamic registration accepts any URL, so anyone could
# register a client and hand out /authorize links on this domain that end on their site: an open redirect that
# phishing scanners flag and hosts suspend for. Loopback covers Claude Code on any port.
# ponytail: fixed list, add a client's callback host here (or an env var) to support another MCP client.
CLIENT_REDIRECT_URIS = [
    "http://localhost",
    "http://127.0.0.1",
    "https://claude.ai/*",
    "https://claude.com/*",
    "https://chatgpt.com/*",
    f"{config.APP_URL}/docs/oauth2-redirect",  # the /docs Authorize button
]


def build_auth() -> GitHubProvider | None:
    """None when GitHub isn't configured, which is only allowed locally (mocked sign-in, see MOCK)."""
    if not (config.GITHUB_CLIENT_ID and config.GITHUB_CLIENT_SECRET):
        if config.APP_ENV == "production" or urlsplit(config.APP_URL).hostname not in ("localhost", "127.0.0.1"):
            raise RuntimeError(
                "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set in production and when APP_URL isn't localhost."
            )
        return None
    for name in ("JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY"):
        if not getattr(config, name):
            raise RuntimeError(f"{name} must be set when GitHub OAuth is configured.")
    with warnings.catch_warnings():
        # py-key-value marks its PostgreSQL store unstable and offers no opt-out; uv.lock pins the version.
        warnings.filterwarnings("ignore", "A configured store is unstable", UserWarning)
        store = PostgreSQLStore(url=config.DATABASE_URL)
    return GitHubProvider(
        client_id=config.GITHUB_CLIENT_ID,
        client_secret=config.GITHUB_CLIENT_SECRET,
        base_url=config.APP_URL,
        required_scopes=["read:user"],
        allowed_client_redirect_uris=CLIENT_REDIRECT_URIS,
        jwt_signing_key=config.JWT_SIGNING_KEY,
        # OAuth clients, codes and upstream GitHub tokens, encrypted, in the kv_store table (auto-created).
        client_storage=FernetEncryptionWrapper(
            store,
            source_material=config.STORAGE_ENCRYPTION_KEY,
            salt="quiz-api",
        ),
        require_authorization_consent="remember",
        # A revoked GitHub token keeps working for up to this long; saves a GitHub API call per request.
        cache_ttl_seconds=300,
    )


auth = build_auth()

# Local development without GitHub: every request is this user, no token needed. Tests switch MOCK off.
MOCK = auth is None
MOCK_CLAIMS: dict[str, Any] = {"sub": "dev", "login": "dev", "name": "Dev", "email": None}

# /docs "Authorize" button: signs in through the same GitHub OAuth flow as MCP clients.
SWAGGER_CLIENT_ID = "swagger-ui"
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl="/authorize",
    tokenUrl="/token",
    refreshUrl="/token",
    scopes={"read:user": "Sign in with GitHub"},
    auto_error=False,
)


async def register_swagger_client() -> None:
    """Pre-register /docs as a public PKCE client; Swagger UI doesn't do dynamic registration."""
    if not auth:
        return
    await auth.register_client(
        OAuthClientInformationFull(
            client_id=SWAGGER_CLIENT_ID,
            client_name="Quiz API docs",
            redirect_uris=[AnyUrl(f"{config.APP_URL}/docs/oauth2-redirect")],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            scope="read:user",
        )
    )


def resolve_user(db: Session, claims: dict[str, Any]) -> User:
    """Allowlist check, then upsert the users row keyed by GitHub's numeric id (renames can't hijack accounts)."""
    login = str(claims.get("login") or "")
    if not MOCK and login.lower() not in config.ALLOWED_USERS:
        raise Forbidden(f"GitHub user {login!r} is not allowed to use this server.")
    profile = {"login": login, "name": claims.get("name"), "email": claims.get("email")}
    user_id = db.execute(
        insert(User)
        .values(provider="github", subject=str(claims["sub"]), **profile)
        .on_conflict_do_update(index_elements=["provider", "subject"], set_=profile)
        .returning(User.id)
    ).scalar_one()
    db.commit()
    return db.get_one(User, user_id, populate_existing=True)


async def token_claims(token: str | None) -> dict[str, Any] | None:
    """Claims of a valid access token we issued, else None. Always the dev user when sign-in is mocked."""
    if MOCK:
        return MOCK_CLAIMS
    access = await auth.load_access_token(token) if auth and token else None
    return access.claims if access else None


async def bearer_claims(token: str | None = Depends(oauth2_scheme)) -> dict[str, Any]:
    claims = await token_claims(token)
    if claims is None:
        raise HTTPException(
            401,
            "Missing or invalid bearer token.",
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{config.APP_URL}/.well-known/oauth-protected-resource/mcp"'
            },
        )
    return claims


def current_user(claims: dict[str, Any] = Depends(bearer_claims), db: Session = Depends(get_db)) -> User:
    return resolve_user(db, claims)

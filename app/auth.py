"""GitHub sign-in via FastMCP's OAuth proxy, and mapping token claims to users rows."""

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User
from app.services import Forbidden


def build_auth() -> GitHubProvider | None:
    """None when GitHub isn't configured: every request is then rejected as unauthenticated."""
    if not (config.GITHUB_CLIENT_ID and config.GITHUB_CLIENT_SECRET):
        return None
    for name in ("JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY"):
        if not getattr(config, name):
            raise RuntimeError(f"{name} must be set when GitHub OAuth is configured.")
    return GitHubProvider(
        client_id=config.GITHUB_CLIENT_ID,
        client_secret=config.GITHUB_CLIENT_SECRET,
        base_url=config.APP_URL,
        required_scopes=["read:user"],
        jwt_signing_key=config.JWT_SIGNING_KEY,
        # OAuth clients, codes and upstream GitHub tokens, encrypted, in the kv_store table (auto-created).
        client_storage=FernetEncryptionWrapper(
            PostgreSQLStore(url=config.DATABASE_URL),
            source_material=config.STORAGE_ENCRYPTION_KEY,
            salt="quiz-api",
        ),
        require_authorization_consent="remember",
        # A revoked GitHub token keeps working for up to this long; saves a GitHub API call per request.
        cache_ttl_seconds=300,
    )


auth = build_auth()


def resolve_user(db: Session, claims: dict[str, Any]) -> User:
    """Allowlist check, then upsert the users row keyed by GitHub's numeric id (renames can't hijack accounts)."""
    login = str(claims.get("login") or "")
    if login.lower() not in config.ALLOWED_USERS:
        raise Forbidden(f"GitHub user {login!r} is not allowed to use this server.")
    profile = {"login": login, "name": claims.get("name"), "email": claims.get("email")}
    user_id = db.scalar(
        insert(User)
        .values(provider="github", subject=str(claims["sub"]), **profile)
        .on_conflict_do_update(index_elements=["provider", "subject"], set_=profile)
        .returning(User.id)
    )
    db.commit()
    return db.get(User, user_id, populate_existing=True)


async def bearer_claims(request: Request) -> dict[str, Any]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    access = await auth.load_access_token(token) if auth and scheme.lower() == "bearer" and token else None
    if access is None:
        raise HTTPException(
            401,
            "Missing or invalid bearer token.",
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{config.APP_URL}/.well-known/oauth-protected-resource/mcp"'
            },
        )
    return access.claims


def current_user(claims: dict[str, Any] = Depends(bearer_claims), db: Session = Depends(get_db)) -> User:
    return resolve_user(db, claims)

"""Server-rendered pages: the home page, with GitHub sign-in with a server-side PKCE flow.

The browser only ever holds HttpOnly cookies: the session (our access token) and, during sign-in, the OAuth state
and PKCE verifier.
"""

import secrets
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import auth as auth_module, config
from app.auth import WEB_CLIENT_ID, WEB_REDIRECT_URI, exchange_code, pkce_challenge, resolve_user, token_claims
from app.db import get_db
from app.models import User
from app.services import Forbidden

SESSION_COOKIE = "quiz_session"
LOGIN_COOKIE = "quiz_login"  # "<state>.<PKCE verifier>" while a sign-in is in flight
SECURE_COOKIES = config.APP_URL.startswith("https://")
TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

router = APIRouter(include_in_schema=False)
Db = Annotated[Session, Depends(get_db)]


def render(name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    page = TEMPLATES.get_template(name).render(app_url=config.APP_URL, **context)
    return HTMLResponse(page, status_code, headers={"Cache-Control": "no-store"})


async def session_claims(request: Request) -> dict[str, Any] | None:
    return await token_claims(request.cookies.get(SESSION_COOKIE))


Claims = Annotated[dict[str, Any] | None, Depends(session_claims)]


def session_user(claims: Claims, db: Db) -> User | None:
    """The signed-in user, or None when there's no valid session or the login is no longer allowlisted."""
    if claims is None:
        return None
    try:
        return resolve_user(db, claims)
    except Forbidden:
        return None


SessionUser = Annotated[User | None, Depends(session_user)]


def set_cookie(response: Response, key: str, value: str, max_age: int | None, path: str = "/") -> None:
    response.set_cookie(key, value, max_age=max_age, path=path, httponly=True, secure=SECURE_COOKIES, samesite="lax")


def to_home() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


def home_page(user: User | None, token: str = "", error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return render(
        "home.html",
        status_code,
        user=user,
        token=token,
        error=error,
        plugin_marketplace=config.PLUGIN_MARKETPLACE,
        mock=auth_module.MOCK,
    )


def login_error(error: str, status_code: int) -> HTMLResponse:
    """The signed-out home page with the error, ending the sign-in attempt."""
    response = home_page(None, error=error, status_code=status_code)
    response.delete_cookie(LOGIN_COOKIE, path="/login")
    return response


@router.get("/")
def home(request: Request, claims: Claims, user: SessionUser) -> HTMLResponse:
    response = home_page(user, request.cookies.get(SESSION_COOKIE, "") if user is not None else "")
    if claims is not None and user is None:  # valid token, but no longer allowlisted
        response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/login", response_model=None)
async def login(  # pylint: disable=too-many-arguments  # OAuth callback parameters
    request: Request,
    user: SessionUser,
    db: Db,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    """The OAuth redirect target (?code=&state= or ?error=). Sign-in itself starts from the home page."""
    if user is not None or (code is None and not error):
        return to_home()
    if error:
        return login_error(error_description or error, 400)

    if code is None:
        return login_error("Missing authorization code.", 400)
    
    return await finish_login(request, db, code, state)


async def finish_login(request: Request, db: Session, code: str, state: str | None) -> Response:
    """OAuth callback: check state, exchange the code (PKCE), create the account, start the session."""
    saved_state, _, verifier = request.cookies.get(LOGIN_COOKIE, "").partition(".")
    if not verifier or not secrets.compare_digest(saved_state, state or ""):
        return login_error("Sign-in expired or was tampered with. Try again.", 400)
    token = await exchange_code(code, verifier)
    claims = await token_claims(token.access_token) if token else None
    if token is None or claims is None:
        return login_error("GitHub sign-in failed. Try again.", 400)
    try:
        await run_in_threadpool(resolve_user, db, claims)  # creates the account on first sign-in
    except Forbidden as exc:
        return login_error(f"{exc} Ask the server owner to add you.", 403)

    response = to_home()
    response.delete_cookie(LOGIN_COOKIE, path="/login")
    set_cookie(response, SESSION_COOKIE, token.access_token, max_age=token.expires_in)
    return response


@router.post("/login", response_model=None)
def start_login(user: SessionUser) -> Response:
    """Start the GitHub OAuth flow; the state and PKCE verifier wait in a short-lived cookie."""
    if user is not None:
        return to_home()
    if auth_module.auth is None:
        return login_error("GitHub sign-in isn't configured on this server.", 503)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)  # urlsafe alphabet has no "."
    query = urlencode(
        {
            "response_type": "code",
            "client_id": WEB_CLIENT_ID,
            "redirect_uri": WEB_REDIRECT_URI,
            "scope": "read:user",
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"/authorize?{query}", status_code=303)
    set_cookie(response, LOGIN_COOKIE, f"{state}.{verifier}", max_age=600, path="/login")
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = to_home()
    response.delete_cookie(SESSION_COOKIE)
    return response

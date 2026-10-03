"""Authenticated Jinja + HTMX browser UI at /app. Calls the same services as the REST API."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Annotated, Any, cast
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup, escape
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app import auth as auth_module, config
from app.auth import (
    MOCK_CLAIMS,
    WEB_ACCESS_COOKIE,
    WEB_CLIENT_ID,
    WEB_REDIRECT_URI,
    resolve_user,
    token_claims,
)
from app.db import get_db
from app.models import User
from app.schemas import AnswerIn, Confidence
from app.services import Invalid, NotFound, content, stats, study
from app.web import TEMPLATES

router = APIRouter(prefix="/app", include_in_schema=False)

OAUTH_STATE_COOKIE = "quiz_oauth_state"
OAUTH_VERIFIER_COOKIE = "quiz_oauth_verifier"
OPTION_LABELS = "ABCDEFGHIJ"


def render(name: str, request: Request, *, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    html = TEMPLATES.get_template(name).render(request=request, user=getattr(request.state, "user", None), **ctx)
    return HTMLResponse(html, status_code=status_code)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def secure_cookie() -> bool:
    return not config.APP_URL.startswith("http://localhost") and not config.APP_URL.startswith("http://127.0.0.1")


def set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        WEB_ACCESS_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie(),
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def clear_oauth_cookies(response: Response) -> None:
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/app")
    response.delete_cookie(OAUTH_VERIFIER_COOKIE, path="/app")


class WebAuthRequired(Exception):
    """Browser user needs to sign in; handler redirects to /app/login."""


async def web_claims(request: Request) -> dict[str, Any]:
    """Claims for /app routes. MOCK skips cookies; otherwise require a valid access-token cookie."""
    if auth_module.MOCK:
        return MOCK_CLAIMS
    claims = await token_claims(request.cookies.get(WEB_ACCESS_COOKIE))
    if claims is None:
        raise WebAuthRequired()
    return claims


def web_user(claims: dict[str, Any] = Depends(web_claims), db: Session = Depends(get_db)) -> User:
    return resolve_user(db, claims)


WebUser = Annotated[User, Depends(web_user)]
Db = Annotated[Session, Depends(get_db)]


@router.get("/login")
async def login(next_path: Annotated[str | None, Query(alias="next")] = None) -> Response:
    if auth_module.MOCK:
        return RedirectResponse(safe_next(next_path), status_code=303)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    params = urlencode(
        {
            "response_type": "code",
            "client_id": WEB_CLIENT_ID,
            "redirect_uri": WEB_REDIRECT_URI,
            "scope": "read:user",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"{config.APP_URL}/authorize?{params}", status_code=303)
    for name, value in ((OAUTH_STATE_COOKIE, state), (OAUTH_VERIFIER_COOKIE, verifier)):
        response.set_cookie(name, value, httponly=True, samesite="lax", secure=secure_cookie(), max_age=600, path="/app")
    if next_path and safe_next(next_path) != "/app":
        response.set_cookie(
            "quiz_oauth_next",
            safe_next(next_path),
            httponly=True,
            samesite="lax",
            secure=secure_cookie(),
            max_age=600,
            path="/app",
        )
    return response


def _sign_in_failed(request: Request, message: str) -> HTMLResponse:
    return render("app/error.html", request, status_code=400, title="Sign-in failed", message=message)


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> Response:
    if auth_module.MOCK:
        return RedirectResponse("/app", status_code=303)

    verifier = request.cookies.get(OAUTH_VERIFIER_COOKIE)
    failure: str | None = None
    if error or not code or not state:
        failure = error or "Missing code."
    elif state != request.cookies.get(OAUTH_STATE_COOKIE):
        failure = "Invalid OAuth state."
    elif not verifier:
        failure = "Missing PKCE verifier."
    if failure:
        return _sign_in_failed(request, failure)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://app") as client:
        token_response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": WEB_REDIRECT_URI,
                "client_id": WEB_CLIENT_ID,
                "code_verifier": verifier,
            },
        )
    access_token = token_response.json().get("access_token") if token_response.status_code == 200 else None
    if not isinstance(access_token, str) or not access_token:
        return _sign_in_failed(request, "Could not exchange authorization code.")

    response = RedirectResponse(safe_next(request.cookies.get("quiz_oauth_next")), status_code=303)
    set_access_cookie(response, access_token)
    clear_oauth_cookies(response)
    response.delete_cookie("quiz_oauth_next", path="/app")
    return response


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/app/login", status_code=303)
    response.delete_cookie(WEB_ACCESS_COOKIE, path="/")
    return response


def safe_next(value: str | None) -> str:
    if value and value.startswith("/app") and not value.startswith("//") and "://" not in value:
        return value
    return "/app"


@router.get("")
@router.get("/")
def decks_index(request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    decks = content.list_decks(db, user)
    return render("app/decks.html", request, decks=decks)


@router.get("/decks/{deck_id}")
def deck_detail(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    detail = content.deck_detail(db, user, deck_id)
    performance = stats.performance(db, user, deck_id=deck_id)
    return render("app/deck.html", request, detail=detail, performance=performance)


@router.post("/decks/{deck_id}/sessions")
def start_session(deck_id: int, db: Db, user: WebUser) -> RedirectResponse:
    started = study.start_session(db, user, deck_id)
    return RedirectResponse(f"/app/sessions/{started['session_id']}", status_code=303)


@router.get("/sessions/{session_id}")
def session_page(session_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    payload = study.next_question(db, user, session_id)
    if payload.get("finished"):
        return render("app/session_finished.html", request, session_id=session_id, summary=payload["summary"])
    return render(
        "app/session.html",
        request,
        session_id=session_id,
        session=payload["session"],
        question=payload["question"],
        error=None,
        labeled_options=labeled_options(payload["question"]["options"]),
    )


@router.get("/sessions/{session_id}/next")
def session_next(session_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    """HTMX partial: next question or finished summary."""
    request.state.user = user
    payload = study.next_question(db, user, session_id)
    if payload.get("finished"):
        template = "app/partials/finished.html" if is_htmx(request) else "app/session_finished.html"
        return render(template, request, session_id=session_id, summary=payload["summary"])
    template = "app/partials/question.html" if is_htmx(request) else "app/session.html"
    return render(
        template,
        request,
        session_id=session_id,
        session=payload["session"],
        question=payload["question"],
        error=None,
        labeled_options=labeled_options(payload["question"]["options"]),
    )


@router.post("/sessions/{session_id}/answers")
async def session_answer(session_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    form: FormData = await request.form()
    question_id = int(str(form.get("question_id") or "0"))
    confidence = str(form.get("confidence") or "confident")
    selected = [str(v) for v in form.getlist("selected")]
    try:
        answer = AnswerIn(question_id=question_id, selected=selected, confidence=cast(Confidence, confidence))
        result = study.submit_answer(db, user, session_id, answer)
    except (Invalid, NotFound, ValidationError) as exc:
        payload = study.next_question(db, user, session_id)
        if payload.get("finished"):
            return render(
                "app/partials/finished.html" if is_htmx(request) else "app/session_finished.html",
                request,
                session_id=session_id,
                summary=payload["summary"],
            )
        if isinstance(exc, Invalid):
            message = exc.message
        elif isinstance(exc, ValidationError):
            message = str(exc.errors()[0]["msg"])
        else:
            message = str(exc)
        template = "app/partials/question.html" if is_htmx(request) else "app/session.html"
        return render(
            template,
            request,
            status_code=422,
            session_id=session_id,
            session=payload["session"],
            question=payload["question"],
            error=message,
            labeled_options=labeled_options(payload["question"]["options"]),
        )

    template = "app/partials/result.html" if is_htmx(request) else "app/session_result.html"
    return render(
        template, request, session_id=session_id, result=result, labeled_options=labeled_options(result["question"]["options"])
    )


def labeled_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**option, "label": OPTION_LABELS[i]} for i, option in enumerate(options)]


def _text_blocks(value: str | None) -> Markup:
    """Escape text and keep newlines (stems are Markdown; full MD rendering can come later)."""
    return Markup(str(escape(value or "")).replace("\n", "<br>\n"))


TEMPLATES.filters.setdefault("text_blocks", _text_blocks)

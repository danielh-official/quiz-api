"""Authenticated Jinja + HTMX browser UI at /app. Calls the same services as the REST API."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Annotated, Any, cast
from urllib.parse import urlencode

import httpx
import nh3
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markdown_it import MarkdownIt
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import select
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
from app.models import Card, User
from app.schemas import (
    QUESTION_SHAPES,
    AnswerIn,
    CardUpdate,
    Confidence,
    DeckCreate,
    DeckUpdate,
    OptionIn,
    QuestionIn,
    QuestionType,
    QuestionUpdate,
)
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
    activity = stats.activity(db, user, content.user_decks(db, user))
    return render("app/decks.html", request, decks=[{**d, **activity[d["id"]]} for d in decks])


@router.get("/archived")
def archived_decks(request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    decks = content.list_decks(db, user, archived=True)
    return render("app/archived.html", request, decks=decks)


@router.get("/decks/new")
def new_deck_form(request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    return render(
        "app/deck_form.html",
        request,
        mode="create",
        deck=None,
        parents=content.list_decks(db, user),
        error=None,
    )


@router.post("/decks/new")
async def create_deck(request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        data = _deck_create_from_form(form)
        deck = content.create_deck(db, user, data)
    except (Invalid, ValidationError) as exc:
        return render(
            "app/deck_form.html",
            request,
            status_code=422,
            mode="create",
            deck=_deck_form_values(form),
            parents=content.list_decks(db, user),
            error=_form_error(exc),
        )
    return RedirectResponse(f"/app/decks/{deck.id}", status_code=303)


@router.get("/decks/{deck_id}")
def deck_detail(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    detail = content.deck_detail(db, user, deck_id)
    performance = stats.performance(db, user, deck_id=deck_id)
    return render("app/deck.html", request, detail=detail, performance=performance)


@router.get("/decks/{deck_id}/archived")
def deck_archived_children(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    detail = content.deck_detail(db, user, deck_id, archived_children=True)
    return render("app/deck_archived.html", request, detail=detail)


@router.get("/decks/{deck_id}/suspended")
def deck_suspended_cards(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    detail = content.deck_detail(db, user, deck_id, suspended=True)
    return render("app/deck_suspended.html", request, detail=detail)


@router.get("/decks/{deck_id}/edit")
def edit_deck_form(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    deck = content.deck_dict(content.get_deck(db, user, deck_id))
    parents = [d for d in content.list_decks(db, user) if d["id"] != deck_id]
    return render("app/deck_form.html", request, mode="edit", deck=deck, parents=parents, error=None)


@router.post("/decks/{deck_id}/edit")
async def update_deck(deck_id: int, request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        data = _deck_update_from_form(form)
        content.update_deck(db, user, deck_id, data)
    except (Invalid, NotFound, ValidationError) as exc:
        parents = [d for d in content.list_decks(db, user) if d["id"] != deck_id]
        return render(
            "app/deck_form.html",
            request,
            status_code=422,
            mode="edit",
            deck={"id": deck_id, **_deck_form_values(form)},
            parents=parents,
            error=_form_error(exc),
        )
    return RedirectResponse(f"/app/decks/{deck_id}", status_code=303)


@router.post("/decks/{deck_id}/delete")
def delete_deck(deck_id: int, db: Db, user: WebUser) -> RedirectResponse:
    content.delete_deck(db, user, deck_id)
    return RedirectResponse("/app", status_code=303)


@router.post("/decks/{deck_id}/sessions")
def start_session(deck_id: int, db: Db, user: WebUser) -> RedirectResponse:
    started = study.start_session(db, user, deck_id)
    return RedirectResponse(f"/app/sessions/{started['session_id']}", status_code=303)


@router.get("/decks/{deck_id}/questions/new")
def new_question_form(deck_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    deck = content.deck_dict(content.require_active_deck(db, user, deck_id))
    qtype = _question_type(request.query_params.get("type"))
    return render(
        "app/question_form.html",
        request,
        mode="create",
        deck=deck,
        decks=content.list_decks(db, user),
        question=_blank_question(qtype),
        error=None,
    )


@router.post("/decks/{deck_id}/questions/new")
async def create_question(deck_id: int, request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        data = _question_in_from_form(form)
        created = content.create_questions(db, user, deck_id, [data])[0]
    except (Invalid, NotFound, ValidationError, ValueError) as exc:
        deck = content.deck_dict(content.get_deck(db, user, deck_id))
        return render(
            "app/question_form.html",
            request,
            status_code=422,
            mode="create",
            deck=deck,
            decks=content.list_decks(db, user),
            question=_question_form_values(form),
            error=_form_error(exc),
        )
    return RedirectResponse(f"/app/questions/{created.id}", status_code=303)


@router.get("/questions/{question_id}")
def question_detail(question_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    question = content.get_question(db, user, question_id)
    deck = content.deck_dict(content.get_deck(db, user, question.deck_id))
    card = db.scalar(select(Card).where(Card.user_id == user.id, Card.question_id == question.id))
    card_view = {
        "note": card.note if card else None,
        "suspended": bool(card and card.suspended_at is not None),
        "reps": card.reps if card else 0,
        "lapses": card.lapses if card else 0,
        "due_at": card.due_at.isoformat() if card and card.due_at else None,
    }
    return render(
        "app/question.html",
        request,
        question=content.describe(question),
        deck=deck,
        card=card_view,
        labeled_options=labeled_options(question.options),
        error=None,
    )


@router.get("/questions/{question_id}/edit")
def edit_question_form(question_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    question = content.get_question(db, user, question_id)
    deck = content.deck_dict(content.get_deck(db, user, question.deck_id))
    qtype = _question_type(request.query_params.get("type"), default=question.type)
    described = content.describe(question)
    if qtype != question.type:
        described = {**described, "type": qtype, "options": _pad_options(described["options"], qtype)}
    return render(
        "app/question_form.html",
        request,
        mode="edit",
        deck=deck,
        decks=content.list_decks(db, user),
        question=described,
        error=None,
    )


@router.post("/questions/{question_id}/edit")
async def update_question(question_id: int, request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        data = _question_update_from_form(form)
        content.update_question(db, user, question_id, data)
    except (Invalid, NotFound, ValidationError, ValueError) as exc:
        question = content.get_question(db, user, question_id)
        deck = content.deck_dict(content.get_deck(db, user, question.deck_id))
        return render(
            "app/question_form.html",
            request,
            status_code=422,
            mode="edit",
            deck=deck,
            decks=content.list_decks(db, user),
            question={"id": question_id, **_question_form_values(form)},
            error=_form_error(exc),
        )
    return RedirectResponse(f"/app/questions/{question_id}", status_code=303)


@router.post("/questions/{question_id}/delete")
def delete_question(question_id: int, db: Db, user: WebUser) -> RedirectResponse:
    question = content.get_question(db, user, question_id)
    deck_id = question.deck_id
    content.delete_question(db, user, question_id)
    return RedirectResponse(f"/app/decks/{deck_id}", status_code=303)


@router.post("/questions/{question_id}/card")
async def update_card(question_id: int, request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        content.update_card(
            db,
            user,
            question_id,
            CardUpdate(
                note=str(form.get("note") or ""),
                suspended=str(form.get("suspended") or "") == "on",
            ),
        )
    except (Invalid, NotFound, ValidationError) as exc:
        question = content.get_question(db, user, question_id)
        deck = content.deck_dict(content.get_deck(db, user, question.deck_id))
        card = db.scalar(select(Card).where(Card.user_id == user.id, Card.question_id == question.id))
        return render(
            "app/question.html",
            request,
            status_code=422,
            question=content.describe(question),
            deck=deck,
            card={
                "note": str(form.get("note") or "") or None,
                "suspended": str(form.get("suspended") or "") == "on",
                "reps": card.reps if card else 0,
                "lapses": card.lapses if card else 0,
                "due_at": card.due_at.isoformat() if card and card.due_at else None,
            },
            labeled_options=labeled_options(question.options),
            error=_form_error(exc),
        )
    return RedirectResponse(f"/app/questions/{question_id}", status_code=303)


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


def _form_error(exc: Exception) -> str:
    if isinstance(exc, Invalid):
        return exc.message
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        msg = str(err["msg"])
        return msg.removeprefix("Value error, ")
    if isinstance(exc, ValueError):
        return str(exc)
    return str(exc)


def _optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    return int(text)


def _deck_form_values(form: FormData) -> dict[str, Any]:
    return {
        "name": str(form.get("name") or ""),
        "description": str(form.get("description") or "") or None,
        "parent_id": _optional_int(form.get("parent_id")),
        "session_size": int(str(form.get("session_size") or "20")),
        "new_per_day": int(str(form.get("new_per_day") or "20")),
        "archived": str(form.get("archived") or "") == "on",
    }


def _deck_create_from_form(form: FormData) -> DeckCreate:
    values = _deck_form_values(form)
    return DeckCreate(
        name=values["name"],
        description=values["description"],
        parent_id=values["parent_id"],
        session_size=values["session_size"],
        new_per_day=values["new_per_day"],
    )


def _deck_update_from_form(form: FormData) -> DeckUpdate:
    values = _deck_form_values(form)
    # Archiving with other fields in one PATCH is rejected; archive alone.
    if values["archived"]:
        return DeckUpdate(archived=True)
    return DeckUpdate(
        name=values["name"],
        description=values["description"],
        parent_id=values["parent_id"],
        session_size=values["session_size"],
        new_per_day=values["new_per_day"],
        archived=False,
    )


def _question_type(value: object, default: QuestionType = "single") -> QuestionType:
    text = str(value or default)
    return cast(QuestionType, text if text in QUESTION_SHAPES else default)


def _blank_question(qtype: QuestionType) -> dict[str, Any]:
    count, _ = QUESTION_SHAPES[qtype]
    return {
        "type": qtype,
        "stem": "",
        "explanation": None,
        "deck_id": None,
        "options": [{"id": None, "text": "", "correct": False, "explanation": None} for _ in range(count)],
        "reset_progress": False,
    }


def _pad_options(options: list[dict[str, Any]], qtype: QuestionType) -> list[dict[str, Any]]:
    count, _ = QUESTION_SHAPES[qtype]
    padded = [
        {
            "id": o.get("id"),
            "text": o.get("text") or "",
            "correct": bool(o.get("correct")),
            "explanation": o.get("explanation"),
        }
        for o in options[:count]
    ]
    while len(padded) < count:
        padded.append({"id": None, "text": "", "correct": False, "explanation": None})
    return padded


def _question_form_values(form: FormData) -> dict[str, Any]:
    qtype = _question_type(form.get("type"))
    count, _ = QUESTION_SHAPES[qtype]
    if qtype == "single":
        correct_raw = str(form.get("correct") or "")
        correct_idxs = {int(correct_raw)} if correct_raw.isdigit() else set()
    else:
        correct_idxs = {int(str(v)) for v in form.getlist("correct") if str(v).isdigit()}
    options = []
    for i in range(count):
        options.append(
            {
                "id": str(form.get(f"option_id_{i}") or "") or None,
                "text": str(form.get(f"option_text_{i}") or ""),
                "correct": i in correct_idxs,
                "explanation": str(form.get(f"option_explanation_{i}") or "") or None,
            }
        )
    return {
        "type": qtype,
        "stem": str(form.get("stem") or ""),
        "explanation": str(form.get("explanation") or "") or None,
        "deck_id": _optional_int(form.get("deck_id")),
        "options": options,
        "reset_progress": str(form.get("reset_progress") or "") == "on",
    }


def _question_in_from_form(form: FormData) -> QuestionIn:
    values = _question_form_values(form)
    return QuestionIn(
        type=values["type"],
        stem=values["stem"],
        explanation=values["explanation"],
        options=[OptionIn(**o) for o in values["options"]],
    )


def _question_update_from_form(form: FormData) -> QuestionUpdate:
    values = _question_form_values(form)
    return QuestionUpdate(
        type=values["type"],
        stem=values["stem"],
        explanation=values["explanation"],
        deck_id=values["deck_id"],
        options=[OptionIn(**o) for o in values["options"]],
        reset_progress=values["reset_progress"],
    )


_MD = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable("strikethrough")
# CommonMark + strikethrough; keep in sync with what markdown-it emits.
_MD_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}


def _markdown(value: str | None, inline: bool = False) -> Markup:
    """Render author Markdown to sanitized HTML for stems, options and explanations."""
    if not value:
        return Markup("")
    html = _MD.renderInline(value) if inline else _MD.render(value)
    return Markup(nh3.clean(html, tags=_MD_TAGS))  # pylint: disable=no-member  # nh3 is a native module


_CONFIDENCE_LABELS = {
    "confident": "Confident",
    "educated_guess": "Educated Guess",
    "complete_guess": "Complete Guess",
}


def _confidence_label(value: str | None) -> str:
    if not value:
        return ""
    return _CONFIDENCE_LABELS.get(value, value.replace("_", " ").title())


TEMPLATES.filters.setdefault("markdown", _markdown)
TEMPLATES.filters.setdefault("confidence_label", _confidence_label)

"""MCP server: thin tools over the service layer. INSTRUCTIONS is the guide MCP clients read."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from pydantic import Field, ValidationError
from sqlalchemy.orm import Session

from app import auth as auth_module
from app.auth import auth, resolve_user
from app.db import SessionLocal
from app.models import User
from app.schemas import (
    AnswerIn,
    CardUpdate,
    Confidence,
    DeckCreate,
    DeckUpdate,
    OptionIn,
    QuestionIn,
    QuestionType,
    QuestionUpdate,
    SettingsUpdate,
)
from app.services import Forbidden, Invalid, NotFound, content, stats, study

INSTRUCTIONS = """\
Quiz API is the user's spaced-repetition quiz app for multiple-choice questions.

- Decks nest; studying, searching and stats on a deck include its subdecks.
- Questions are "single" (4 options, 1 correct) or "select_two" (5 options, 2 correct). Text is Markdown.
  Options have stable ids; answers reference option ids.
- Answers carry a confidence: confident, educated_guess or complete_guess. FSRS scheduling:
  correct+confident = Good, correct+educated_guess = Hard, anything else = Again.
  A wrong answer given with confidence is a "misconception".

Studying: start-session, then loop next-question -> show "Question N of M", the stem as bullets (one sentence each),
a blank line, then the options as "A. ...", "B. ..." on separate lines, wording and order exactly as returned,
without hinting -> ask for the pick(s) and confidence -> submit-answer -> present the result and explanations.
Stop when next-question says the session is finished. On first use, check the user's timezone (update-settings).

Writing questions: search-questions first to avoid duplicates; test understanding, not trivia; plausible
distractors of similar length; an explanation on every option plus an overall explanation.
"""

mcp = FastMCP("Quiz API", instructions=INSTRUCTIONS, auth=auth)

READ = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False}
IDEMPOTENT = {**WRITE, "idempotentHint": True}


def error_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())


@contextmanager
def caller() -> Iterator[tuple[Session, User]]:
    """DB session + the authenticated user; service errors become tool errors the model can act on."""
    token = get_access_token()
    if token is None and not auth_module.MOCK:
        raise ToolError("Not authenticated.")
    claims = token.claims if token is not None else auth_module.MOCK_CLAIMS
    with SessionLocal() as db:
        try:
            yield db, resolve_user(db, claims)
        except (NotFound, Invalid, Forbidden) as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(error_message(exc)) from exc


@mcp.tool(name="list-decks", annotations=READ)
def list_decks() -> dict[str, Any]:
    """List every deck as a tree (depth-first) with due/new/total question counts that include subdecks."""
    with caller() as (db, user):
        return {"decks": content.list_decks(db, user)}


@mcp.tool(name="get-deck", annotations=READ)
def get_deck(deck_id: int, page: Annotated[int, Field(ge=1, description="Page of questions, from 1.")] = 1) -> dict[str, Any]:
    """Get a deck with its settings, path, direct subdecks and the questions stored directly in it
    (answers and explanations included), 50 per page."""
    with caller() as (db, user):
        return content.deck_detail(db, user, deck_id, page)


@mcp.tool(name="search-questions", annotations=READ)
def search_questions(
    query: Annotated[str, Field(min_length=2, max_length=200)],
    deck_id: Annotated[int | None, Field(description="Limit to this deck and its subdecks.")] = None,
) -> dict[str, Any]:
    """Case-insensitive substring search over stems, option texts and explanations (max 25 results).
    Use it before creating questions to avoid duplicates."""
    with caller() as (db, user):
        return content.search_questions(db, user, query, deck_id)


@mcp.tool(name="get-performance", annotations=READ)
def get_performance(
    deck_id: Annotated[int | None, Field(description="Deck to report on (with subdecks). Omit for all decks.")] = None,
) -> dict[str, Any]:
    """The user's performance: due/new counts, calibration (accuracy per confidence level), recent misconceptions
    (wrong while confident), most-forgotten questions (leeches) and the user's notes. Use it to find weak spots."""
    with caller() as (db, user):
        return stats.performance(db, user, deck_id)


@mcp.tool(name="create-deck", annotations=WRITE)
def create_deck(
    name: str,
    description: str | None = None,
    parent_id: Annotated[int | None, Field(description="Deck to nest this one under.")] = None,
    session_size: Annotated[int, Field(description="Default questions per session (1-500).")] = 20,
    new_per_day: Annotated[int, Field(description="New questions introduced per day (0-1000).")] = 20,
) -> dict[str, Any]:
    """Create a deck, optionally nested under another deck."""
    with caller() as (db, user):
        data = DeckCreate(
            name=name, description=description, parent_id=parent_id, session_size=session_size, new_per_day=new_per_day
        )
        return {"deck": content.deck_dict(content.create_deck(db, user, data))}


@mcp.tool(name="update-deck", annotations=IDEMPOTENT)
def update_deck(
    deck_id: int,
    name: str | None = None,
    description: str | None = None,
    parent_id: Annotated[int | None, Field(description="New parent deck, or 0 to make it top-level.")] = None,
    session_size: int | None = None,
    new_per_day: int | None = None,
) -> dict[str, Any]:
    """Rename, re-describe, move or change the study settings of a deck. Omitted fields stay unchanged."""
    with caller() as (db, user):
        given = {"name": name, "description": description, "session_size": session_size, "new_per_day": new_per_day}
        changes: dict[str, Any] = {k: v for k, v in given.items() if v is not None}
        if parent_id is not None:
            changes["parent_id"] = parent_id or None
        return {"deck": content.deck_dict(content.update_deck(db, user, deck_id, DeckUpdate(**changes)))}


@mcp.tool(name="create-questions", annotations=WRITE)
def create_questions(deck_id: int, questions: Annotated[list[QuestionIn], Field(min_length=1, max_length=50)]) -> dict[str, Any]:
    """Add 1-50 questions to a deck. All are validated first; if any is invalid, none are created."""
    with caller() as (db, user):
        return {"questions": [content.describe(q) for q in content.create_questions(db, user, deck_id, questions)]}


@mcp.tool(name="update-question", annotations={"readOnlyHint": False})
def update_question(
    question_id: int,
    type: QuestionType | None = None,
    stem: str | None = None,
    options: Annotated[
        list[OptionIn] | None, Field(description="Replaces all options. Pass back an option's id to keep its identity.")
    ] = None,
    explanation: str | None = None,
    deck_id: Annotated[int | None, Field(description="Move the question to this deck.")] = None,
    reset_progress: Annotated[
        bool, Field(description="Restart review progress on this question. Use only when what it tests or its answer changed.")
    ] = False,
) -> dict[str, Any]:
    """Edit a question or move it to another deck. Omitted fields stay unchanged."""
    with caller() as (db, user):
        given = {"type": type, "stem": stem, "options": options, "explanation": explanation, "deck_id": deck_id}
        data = QuestionUpdate(reset_progress=reset_progress, **{k: v for k, v in given.items() if v is not None})
        return {"question": content.describe(content.update_question(db, user, question_id, data))}


@mcp.tool(name="start-session", annotations=WRITE)
def start_session(
    deck_id: int,
    size: Annotated[int | None, Field(ge=1, le=500, description="Max questions; defaults to the deck's setting.")] = None,
) -> dict[str, Any]:
    """Start a study session on a deck (with subdecks): due questions first, then new ones within the daily
    limits. Follow with next-question."""
    with caller() as (db, user):
        return study.start_session(db, user, deck_id, size)


@mcp.tool(name="next-question", annotations=WRITE)
def next_question(session_id: int) -> dict[str, Any]:
    """Next question of a session, without its answer, options shuffled. Show them as given, ask for the pick(s)
    and confidence, then call submit-answer with the option ids. Returns a summary when the session is finished."""
    with caller() as (db, user):
        return study.next_question(db, user, session_id)


@mcp.tool(name="submit-answer", annotations=WRITE)
def submit_answer(
    session_id: int,
    question_id: int,
    selected: Annotated[list[str], Field(description='Option ids picked: one for "single", two for "select_two".')],
    confidence: Annotated[Confidence, Field(description="How sure the user was before seeing the answer.")],
) -> dict[str, Any]:
    """Grade the user's answer. Returns whether it was right, the full question with correct options and
    explanations, the user's note and when the question comes back."""
    with caller() as (db, user):
        answer = AnswerIn(question_id=question_id, selected=selected, confidence=confidence)
        return study.submit_answer(db, user, session_id, answer)


@mcp.tool(name="update-card", annotations=IDEMPOTENT)
def update_card(
    question_id: int,
    note: Annotated[str | None, Field(description="Replace the user's private note; empty string deletes it.")] = None,
    suspended: Annotated[bool | None, Field(description="Suspend (skip in study) or unsuspend the question.")] = None,
) -> dict[str, Any]:
    """Save the user's private note on a question (typically their reasoning) and/or suspend it."""
    with caller() as (db, user):
        given = {"note": note, "suspended": suspended}
        return content.update_card(db, user, question_id, CardUpdate(**{k: v for k, v in given.items() if v is not None}))


@mcp.tool(name="update-settings", annotations=IDEMPOTENT)
def update_settings(
    timezone: Annotated[str | None, Field(description="IANA timezone, e.g. America/New_York.")] = None,
    desired_retention: Annotated[float | None, Field(description="Target recall probability, 0.70-0.99.")] = None,
) -> dict[str, Any]:
    """Show the user's settings, or change them. The study day (and daily new-question limits) rolls over at
    4am in this timezone. Call with no arguments to read the current settings."""
    with caller() as (db, user):
        return content.update_settings(db, user, SettingsUpdate(timezone=timezone, desired_retention=desired_retention))

"""Decks, questions and per-user cards."""

import random
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, func, literal_column, or_, select, update
from sqlalchemy.orm import Session

from app.models import Card, Deck, Question, User
from app.schemas import (
    QUESTION_SHAPES,
    CardUpdate,
    DeckCreate,
    DeckUpdate,
    OptionIn,
    QuestionIn,
    QuestionUpdate,
    SettingsUpdate,
)
from app.services import Invalid, NotFound

QUESTIONS_PER_PAGE = 50
SEARCH_LIMIT = 25


# --- deck tree -------------------------------------------------------------------------------------


def user_decks(db: Session, user: User) -> dict[int, Deck]:
    # ponytail: loads the user's whole deck tree per call; fine for hundreds of decks, switch to recursive CTEs beyond.
    return {d.id: d for d in db.scalars(select(Deck).where(Deck.user_id == user.id))}


def children_of(decks: dict[int, Deck]) -> dict[int | None, list[Deck]]:
    children: dict[int | None, list[Deck]] = {}
    for deck in sorted(decks.values(), key=lambda d: (d.name.lower(), d.id)):
        children.setdefault(deck.parent_id, []).append(deck)
    return children


def subtree_ids(decks: dict[int, Deck], root_id: int) -> list[int]:
    children = children_of(decks)
    ids, stack = [], [root_id]
    while stack:
        deck_id = stack.pop()
        ids.append(deck_id)
        stack.extend(child.id for child in children.get(deck_id, []))
    return ids


def path_names(decks: dict[int, Deck], deck_id: int) -> list[str]:
    names = []
    while deck_id is not None:
        names.append(decks[deck_id].name)
        deck_id = decks[deck_id].parent_id
    return names[::-1]


def get_deck(db: Session, user: User, deck_id: int) -> Deck:
    deck = db.get(Deck, deck_id)
    if deck is None or deck.user_id != user.id:
        raise NotFound(f"Deck {deck_id} not found.")
    return deck


def get_question(db: Session, user: User, question_id: int) -> Question:
    question = db.scalar(select(Question).join(Deck).where(Question.id == question_id, Deck.user_id == user.id))
    if question is None:
        raise NotFound(f"Question {question_id} not found.")
    return question


def deck_dict(deck: Deck) -> dict[str, Any]:
    return {
        "id": deck.id,
        "name": deck.name,
        "description": deck.description,
        "parent_id": deck.parent_id,
        "session_size": deck.session_size,
        "new_per_day": deck.new_per_day,
    }


def list_decks(db: Session, user: User) -> list[dict[str, Any]]:
    """Every deck, depth-first, with due/new/total counts that include subdecks."""
    from app.services.stats import counts

    decks = user_decks(db, user)
    per_deck = counts(db, user, decks)
    children = children_of(decks)
    out: list[dict[str, Any]] = []

    def walk(parent_id: int | None, depth: int) -> None:
        for deck in children.get(parent_id, []):
            out.append({"id": deck.id, "name": deck.name, "parent_id": deck.parent_id, "depth": depth, **per_deck[deck.id]})
            walk(deck.id, depth + 1)

    walk(None, 0)
    return out


def deck_detail(db: Session, user: User, deck_id: int, page: int = 1) -> dict[str, Any]:
    """Deck settings, path, direct subdecks and a page of its own questions (with answers)."""
    from app.services.stats import counts

    deck = get_deck(db, user, deck_id)
    decks = user_decks(db, user)
    page = max(page, 1)
    total = db.scalar(select(func.count()).where(Question.deck_id == deck.id))
    questions = db.scalars(
        select(Question)
        .where(Question.deck_id == deck.id)
        .order_by(Question.id)
        .offset((page - 1) * QUESTIONS_PER_PAGE)
        .limit(QUESTIONS_PER_PAGE)
    )
    return {
        "deck": {**deck_dict(deck), "path": path_names(decks, deck.id), **counts(db, user, decks)[deck.id]},
        "subdecks": [deck_dict(d) for d in children_of(decks).get(deck.id, [])],
        "questions": [describe(q) for q in questions],
        "page": page,
        "pages": max(1, -(-total // QUESTIONS_PER_PAGE)),
    }


def check_parent(db: Session, user: User, parent_id: int | None, deck: Deck | None = None) -> None:
    if parent_id is None:
        return
    decks = user_decks(db, user)
    if parent_id not in decks:
        raise Invalid("parent_id", f"Deck {parent_id} not found.")
    if deck is not None and parent_id in subtree_ids(decks, deck.id):
        raise Invalid("parent_id", "A deck cannot be nested inside itself or one of its subdecks.")


def create_deck(db: Session, user: User, data: DeckCreate) -> Deck:
    check_parent(db, user, data.parent_id)
    deck = Deck(user_id=user.id, **data.model_dump())
    db.add(deck)
    db.commit()
    return deck


def update_deck(db: Session, user: User, deck_id: int, data: DeckUpdate) -> Deck:
    deck = get_deck(db, user, deck_id)
    changes = data.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        check_parent(db, user, changes["parent_id"], deck)
    for field in ("name", "session_size", "new_per_day"):
        if field in changes and changes[field] is None:
            raise Invalid(field, "Cannot be null.")
    for field, value in changes.items():
        setattr(deck, field, value)
    db.commit()
    return deck


def delete_deck(db: Session, user: User, deck_id: int) -> None:
    db.delete(get_deck(db, user, deck_id))  # FK cascades remove subdecks, questions, cards, reviews
    db.commit()


# --- questions -------------------------------------------------------------------------------------


def describe(question: Question, with_answers: bool = True, shuffle: bool = False) -> dict[str, Any]:
    """Question as returned to callers. Without answers: only what a student may see before answering."""
    options = [o if with_answers else {"id": o["id"], "text": o["text"]} for o in question.options]
    if shuffle:
        options = random.sample(options, len(options))
    out = {
        "id": question.id,
        "deck_id": question.deck_id,
        "type": question.type,
        "pick": QUESTION_SHAPES[question.type][1],
        "stem": question.stem,
        "options": options,
    }
    if with_answers:
        out["explanation"] = question.explanation
    return out


def option_rows(options: list[OptionIn], existing_ids: set[str]) -> list[dict[str, Any]]:
    """Keep ids of options passed back with a known id; mint fresh ids for the rest."""
    used: set[str] = set()
    rows = []
    for option in options:
        option_id = option.id if option.id in existing_ids and option.id not in used else None
        while option_id is None or option_id in used or (option_id in existing_ids and option_id != option.id):
            option_id = secrets.token_hex(3)
        used.add(option_id)
        row = {"id": option_id, "text": option.text, "correct": option.correct}
        if option.explanation:
            row["explanation"] = option.explanation
        rows.append(row)
    return rows


def create_questions(db: Session, user: User, deck_id: int, items: list[QuestionIn]) -> list[Question]:
    deck = get_deck(db, user, deck_id)
    questions = [
        Question(
            deck_id=deck.id,
            type=item.type,
            stem=item.stem,
            options=option_rows(item.options, set()),
            explanation=item.explanation,
        )
        for item in items
    ]
    db.add_all(questions)
    db.commit()
    return questions


def update_question(db: Session, user: User, question_id: int, data: QuestionUpdate) -> Question:
    question = get_question(db, user, question_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("deck_id") is not None:
        question.deck_id = get_deck(db, user, changes["deck_id"]).id
    merged = QuestionIn(
        type=data.type or question.type,
        stem=data.stem if data.stem is not None else question.stem,
        options=data.options if data.options is not None else [OptionIn(**o) for o in question.options],
        explanation=data.explanation if "explanation" in changes else question.explanation,
    )  # raises pydantic.ValidationError on a bad result
    question.type = merged.type
    question.stem = merged.stem
    question.options = option_rows(merged.options, {o["id"] for o in question.options})
    question.explanation = merged.explanation
    if data.reset_progress:
        # Keeps notes and suspensions; everyone starts this question over.
        db.execute(
            update(Card)
            .where(Card.question_id == question.id)
            .values(stability=None, difficulty=None, due_at=None, last_reviewed_at=None, reps=0, lapses=0)
        )
    db.commit()
    return question


def delete_question(db: Session, user: User, question_id: int) -> None:
    db.delete(get_question(db, user, question_id))
    db.commit()


def search_questions(db: Session, user: User, query: str, deck_id: int | None = None) -> dict[str, Any]:
    """Case-insensitive substring search over stems, option texts and explanations."""
    query = query.strip()
    if not 2 <= len(query) <= 200:
        raise Invalid("query", "Must be 2-200 characters.")
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    option_texts = cast(func.jsonb_path_query_array(Question.options, literal_column("'$[*].text'::jsonpath")), Text)
    stmt = (
        select(Question)
        .join(Deck)
        .where(
            Deck.user_id == user.id,
            or_(
                Question.stem.ilike(pattern, escape="\\"),
                option_texts.ilike(pattern, escape="\\"),
                Question.explanation.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Question.id)
        .limit(SEARCH_LIMIT + 1)
    )
    if deck_id is not None:
        stmt = stmt.where(Question.deck_id.in_(subtree_ids(user_decks(db, user), get_deck(db, user, deck_id).id)))
    found = list(db.scalars(stmt))
    return {"questions": [describe(q) for q in found[:SEARCH_LIMIT]], "truncated": len(found) > SEARCH_LIMIT}


# --- cards -----------------------------------------------------------------------------------------


def card_for(db: Session, user: User, question: Question) -> Card:
    card = db.scalar(select(Card).where(Card.user_id == user.id, Card.question_id == question.id))
    if card is None:
        card = Card(user_id=user.id, question_id=question.id, reps=0, lapses=0)
        db.add(card)
    return card


def update_card(db: Session, user: User, question_id: int, data: CardUpdate) -> dict[str, Any]:
    card = card_for(db, user, get_question(db, user, question_id))
    changes = data.model_dump(exclude_unset=True)
    if "note" in changes:
        card.note = (changes["note"] or "").strip() or None
    if changes.get("suspended") is not None:
        card.suspended_at = (card.suspended_at or datetime.now(UTC)) if changes["suspended"] else None
    db.commit()
    return {"question_id": question_id, "note": card.note, "suspended": card.suspended_at is not None}


# --- account ---------------------------------------------------------------------------------------


def user_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "login": user.login,
        "name": user.name,
        "email": user.email,
        "timezone": user.timezone,
        "desired_retention": user.desired_retention,
    }


def update_settings(db: Session, user: User, data: SettingsUpdate) -> dict[str, Any]:
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(user, field, value)
    db.commit()
    return user_dict(user)


def delete_user(db: Session, user: User) -> None:
    db.delete(user)  # FK cascades remove decks, questions, cards, sessions, reviews
    db.commit()

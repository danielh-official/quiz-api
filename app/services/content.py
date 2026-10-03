"""Decks, questions and per-user cards."""

import random
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Text, cast, func, literal_column, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.models import Card, Deck, Question, Review, User
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
ArchiveFilter = Literal["active", "archived", "all"]


# --- deck tree -------------------------------------------------------------------------------------


def user_decks(db: Session, user: User) -> dict[int, Deck]:
    # ponytail: loads the user's whole deck tree per call; fine for hundreds of decks, switch to recursive CTEs beyond.
    return {d.id: d for d in db.scalars(select(Deck).where(Deck.user_id == user.id))}


def children_of(decks: dict[int, Deck]) -> dict[int | None, list[Deck]]:
    children: dict[int | None, list[Deck]] = {}
    for deck in sorted(decks.values(), key=lambda d: (d.name.lower(), d.id)):
        children.setdefault(deck.parent_id, []).append(deck)
    return children


def effectively_archived(decks: dict[int, Deck], deck_id: int) -> bool:
    """True if this deck or any ancestor has archived_at set."""
    current: int | None = deck_id
    while current is not None:
        if decks[current].archived_at is not None:
            return True
        current = decks[current].parent_id
    return False


def subtree_ids(decks: dict[int, Deck], root_id: int, *, active_only: bool = False) -> list[int]:
    """Deck ids under root (inclusive). active_only skips directly archived branches (and an archived root)."""
    if active_only and effectively_archived(decks, root_id):
        return []
    children = children_of(decks)
    ids, stack = [], [root_id]
    while stack:
        deck_id = stack.pop()
        ids.append(deck_id)
        for child in children.get(deck_id, []):
            if active_only and child.archived_at is not None:
                continue
            stack.append(child.id)
    return ids


def path_crumbs(decks: dict[int, Deck], deck_id: int) -> list[dict[str, Any]]:
    """Ancestor chain from root to deck_id, each entry {id, name}."""
    crumbs: list[dict[str, Any]] = []
    current: int | None = deck_id
    while current is not None:
        crumbs.append({"id": current, "name": decks[current].name})
        current = decks[current].parent_id
    return crumbs[::-1]


def path_names(decks: dict[int, Deck], deck_id: int) -> list[str]:
    return [c["name"] for c in path_crumbs(decks, deck_id)]


def get_deck(db: Session, user: User, deck_id: int) -> Deck:
    deck = db.get(Deck, deck_id)
    if deck is None or deck.user_id != user.id:
        raise NotFound(f"Deck {deck_id} not found.")
    return deck


def require_active_deck(db: Session, user: User, deck_id: int) -> Deck:
    """get_deck, then reject if the deck is effectively archived."""
    deck = get_deck(db, user, deck_id)
    if effectively_archived(user_decks(db, user), deck.id):
        raise Invalid("deck_id", "Deck is archived.")
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
        "archived": deck.archived_at is not None,
        "archived_at": deck.archived_at.isoformat() if deck.archived_at else None,
    }


def list_decks(db: Session, user: User, archived: bool = False) -> list[dict[str, Any]]:
    """Active deck tree by default, or the forest of directly archived decks when archived=True."""
    from app.services.stats import counts  # pylint: disable=import-outside-toplevel  # import cycle

    decks = user_decks(db, user)
    per_deck = counts(db, user, decks, active_only=not archived)
    children = children_of(decks)
    out: list[dict[str, Any]] = []

    def row(deck: Deck, depth: int) -> dict[str, Any]:
        return {
            "id": deck.id,
            "name": deck.name,
            "parent_id": deck.parent_id,
            "depth": depth,
            "archived": deck.archived_at is not None,
            "archived_at": deck.archived_at.isoformat() if deck.archived_at else None,
            **per_deck[deck.id],
        }

    if not archived:

        def walk_active(parent_id: int | None, depth: int) -> None:
            for deck in children.get(parent_id, []):
                if deck.archived_at is not None:
                    continue
                out.append(row(deck, depth))
                walk_active(deck.id, depth + 1)

        walk_active(None, 0)
        return out

    direct = {d.id for d in decks.values() if d.archived_at is not None}

    def walk_archived(parent_id: int | None, depth: int) -> None:
        for deck in children.get(parent_id, []):
            if deck.id not in direct:
                continue
            out.append(row(deck, depth))
            walk_archived(deck.id, depth + 1)

    # Forest roots: directly archived decks whose parent is not also directly archived.
    for deck in sorted((d for d in decks.values() if d.id in direct), key=lambda d: (d.name.lower(), d.id)):
        if deck.parent_id is None or deck.parent_id not in direct:
            out.append(row(deck, 0))
            walk_archived(deck.id, 1)
    return out


def question_answer_stats(db: Session, user: User, question_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per-question answered/correct counts and last review time for this user."""
    if not question_ids:
        return {}
    rows = db.execute(
        select(
            Review.question_id,
            func.count(),
            func.count().filter(Review.correct),
            func.max(Review.reviewed_at),
        )
        .where(Review.user_id == user.id, Review.question_id.in_(question_ids))
        .group_by(Review.question_id)
    )
    return {
        question_id: {
            "answered": answered,
            "correct": correct,
            "last_answered_at": last.isoformat() if last else None,
        }
        for question_id, answered, correct, last in rows
    }


def deck_detail(  # pylint: disable=too-many-locals
    db: Session,
    user: User,
    deck_id: int,
    page: int = 1,
    *,
    suspended: bool = False,
    archived_children: bool = False,
) -> dict[str, Any]:
    """Deck settings, path, direct subdecks and a page of its own questions (with answers).

    Default: active (non-archived) subdecks and non-suspended questions.
    suspended=True: questions are only this deck's suspended cards.
    archived_children=True: subdecks are only directly archived children.
    """
    from app.services.stats import counts  # pylint: disable=import-outside-toplevel  # import cycle

    deck = get_deck(db, user, deck_id)
    decks = user_decks(db, user)
    page = max(page, 1)
    children = children_of(decks).get(deck.id, [])
    active_children = [d for d in children if d.archived_at is None]
    archived_kids = [d for d in children if d.archived_at is not None]
    subdecks = archived_kids if archived_children else active_children

    if suspended:
        suspend_filter: ColumnElement[bool] = Card.suspended_at.is_not(None)
    else:
        suspend_filter = or_(Card.id.is_(None), Card.suspended_at.is_(None))
    card_join = (Card.question_id == Question.id) & (Card.user_id == user.id)
    total = db.execute(
        select(func.count(Question.id)).outerjoin(Card, card_join).where(Question.deck_id == deck.id, suspend_filter)
    ).scalar_one()
    questions = list(
        db.scalars(
            select(Question)
            .outerjoin(Card, card_join)
            .where(Question.deck_id == deck.id, suspend_filter)
            .order_by(Question.id)
            .offset((page - 1) * QUESTIONS_PER_PAGE)
            .limit(QUESTIONS_PER_PAGE)
        )
    )
    answer_stats = question_answer_stats(db, user, [q.id for q in questions])
    suspended_count = db.execute(
        select(func.count())
        .select_from(Question)
        .join(Card, card_join)
        .where(Question.deck_id == deck.id, Card.suspended_at.is_not(None))
    ).scalar_one()
    return {
        "deck": {
            **deck_dict(deck),
            "path": path_names(decks, deck.id),
            "crumbs": path_crumbs(decks, deck.id),
            **counts(db, user, decks, active_only=True)[deck.id],
            "archived_children_count": len(archived_kids),
            "suspended_count": suspended_count,
        },
        "subdecks": [deck_dict(d) for d in subdecks],
        "questions": [
            {
                **describe(q),
                **answer_stats.get(q.id, {"answered": 0, "correct": 0, "last_answered_at": None}),
            }
            for q in questions
        ],
        "page": page,
        "pages": max(1, -(-total // QUESTIONS_PER_PAGE)),
        "suspended": suspended,
        "archived_children": archived_children,
    }


def check_parent(db: Session, user: User, parent_id: int | None, deck: Deck | None = None) -> None:
    if parent_id is None:
        return
    decks = user_decks(db, user)
    if parent_id not in decks:
        raise Invalid("parent_id", f"Deck {parent_id} not found.")
    if effectively_archived(decks, parent_id):
        raise Invalid("parent_id", "Cannot nest under an archived deck.")
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
    if "archived" in changes:
        archived = changes.pop("archived")
        deck.archived_at = (deck.archived_at or datetime.now(UTC)) if archived else None
    decks = user_decks(db, user)
    decks[deck.id] = deck
    if changes and effectively_archived(decks, deck.id):
        raise Invalid("deck_id", "Unarchive this deck before making other changes.")
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
    out: dict[str, Any] = {
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
    deck = require_active_deck(db, user, deck_id)
    questions = [
        Question(
            deck_id=deck.id,
            type=item.type,
            stem=item.stem,
            # Authors (LLMs especially) tend to put the answer first; don't let stored order leak it.
            options=option_rows(random.sample(item.options, len(item.options)), set()),
            explanation=item.explanation,
        )
        for item in items
    ]
    db.add_all(questions)
    db.commit()
    return questions


def update_question(db: Session, user: User, question_id: int, data: QuestionUpdate) -> Question:
    question = get_question(db, user, question_id)
    require_active_deck(db, user, question.deck_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("deck_id") is not None:
        question.deck_id = require_active_deck(db, user, changes["deck_id"]).id
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
    question = get_question(db, user, question_id)
    require_active_deck(db, user, question.deck_id)
    db.delete(question)
    db.commit()


def search_questions(
    db: Session,
    user: User,
    query: str,
    deck_id: int | None = None,
    archived: ArchiveFilter = "active",
) -> dict[str, Any]:
    """Case-insensitive substring search over stems, option texts and explanations."""
    query = query.strip()
    if not 2 <= len(query) <= 200:
        raise Invalid("query", "Must be 2-200 characters.")
    if archived not in ("active", "archived", "all"):
        raise Invalid("archived", 'Must be "active", "archived", or "all".')
    decks = user_decks(db, user)
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
        get_deck(db, user, deck_id)  # ownership
        stmt = stmt.where(Question.deck_id.in_(subtree_ids(decks, deck_id)))
    if archived != "all":
        want_archived = archived == "archived"
        matching = {did for did in decks if effectively_archived(decks, did) == want_archived}
        stmt = stmt.where(Question.deck_id.in_(matching or {-1}))
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
    question = get_question(db, user, question_id)
    require_active_deck(db, user, question.deck_id)
    card = card_for(db, user, question)
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

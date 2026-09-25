"""Deck counts and performance reports."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Card, Deck, Question, Review, User
from app.services.content import children_of, describe, get_deck, subtree_ids, user_decks
from app.services.study import new_remaining, unstudied


def per_deck(db: Session, stmt) -> dict[int, int]:
    return dict(db.execute(stmt.group_by(Question.deck_id)).all())


def counts(db: Session, user: User, decks: dict[int, Deck]) -> dict[int, dict[str, int]]:
    """due / new / total per deck, each including subdecks. new = what the Anki v3 limits admit today."""
    now = datetime.now(UTC)
    in_decks = Question.deck_id.in_(decks)
    total = per_deck(db, select(Question.deck_id, func.count()).where(in_decks))
    fresh = per_deck(db, select(Question.deck_id, func.count()).where(in_decks, unstudied(user)))
    due = per_deck(
        db,
        select(Question.deck_id, func.count())
        .join(Card, Card.question_id == Question.id)
        .where(
            in_decks,
            Card.user_id == user.id,
            Card.suspended_at.is_(None),
            Card.last_reviewed_at.is_not(None),
            Card.due_at <= now,
        ),
    )
    remaining = new_remaining(db, user, decks, now)
    children = children_of(decks)
    out: dict[int, dict[str, int]] = {}

    def roll_up(deck_id: int) -> dict[str, int]:
        kids = [roll_up(child.id) for child in children.get(deck_id, [])]
        # Nested caps form a laminar family, so this min() equals what the per-question walk admits.
        out[deck_id] = {
            "due": due.get(deck_id, 0) + sum(k["due"] for k in kids),
            "new": min(remaining[deck_id], fresh.get(deck_id, 0) + sum(k["new"] for k in kids)),
            "total": total.get(deck_id, 0) + sum(k["total"] for k in kids),
        }
        return out[deck_id]

    for top in children.get(None, []):
        roll_up(top.id)
    return out


def performance(db: Session, user: User, deck_id: int | None = None) -> dict[str, Any]:
    """Counts, calibration, misconceptions, leeches and notes for one deck (with subdecks) or everything."""
    decks = user_decks(db, user)
    per = counts(db, user, decks)
    if deck_id is not None:
        deck_ids = subtree_ids(decks, get_deck(db, user, deck_id).id)
        totals = per[deck_id]
    else:
        deck_ids = list(decks)
        tops = [per[d.id] for d in children_of(decks).get(None, [])]
        totals = {key: sum(t[key] for t in tops) for key in ("due", "new", "total")}

    in_scope = (Review.user_id == user.id, Question.deck_id.in_(deck_ids))
    calibration = {c: {"answered": 0, "correct": 0} for c in ("confident", "educated_guess", "complete_guess")}
    for confidence, answered, correct in db.execute(
        select(Review.confidence, func.count(), func.count().filter(Review.correct))
        .join(Question, Question.id == Review.question_id)
        .where(*in_scope)
        .group_by(Review.confidence)
    ):
        calibration[confidence] = {"answered": answered, "correct": correct}

    misconceptions = db.execute(
        select(Question, func.max(Review.reviewed_at).label("last"))
        .join(Review, Review.question_id == Question.id)
        .where(*in_scope, Review.correct.is_(False), Review.confidence == "confident")
        .group_by(Question.id)
        .order_by(func.max(Review.reviewed_at).desc())
        .limit(20)
    ).all()
    card_scope = (Card.user_id == user.id, Question.deck_id.in_(deck_ids))
    leeches = db.execute(
        select(Card, Question).join(Question).where(*card_scope, Card.lapses > 0).order_by(Card.lapses.desc()).limit(20)
    ).all()
    notes = db.execute(
        select(Card.note, Question.id, Question.stem).join(Question).where(*card_scope, Card.note.is_not(None)).limit(50)
    ).all()

    return {
        "counts": totals,
        "calibration": calibration,
        "misconceptions": [{"question": describe(q), "last_wrong_at": last.isoformat()} for q, last in misconceptions],
        "leeches": [{"question": describe(q), "lapses": c.lapses, "reps": c.reps} for c, q in leeches],
        "notes": [{"question_id": qid, "stem": stem, "note": note} for note, qid, stem in notes],
    }

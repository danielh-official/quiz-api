"""Study sessions: picking the next question, grading answers and FSRS scheduling."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import fsrs
from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Card, Deck, Question, Review, StudySession, User
from app.schemas import QUESTION_SHAPES, AnswerIn, Confidence, SessionUpdate
from app.services import Invalid, NotFound
from app.services.content import card_for, describe, get_deck, get_question, path_names, subtree_ids, user_decks

RECENT_SUMMARIES = 3  # returned by start_session so the next session can pick up where the last ones left off

FUZZ = True  # interval fuzzing; tests switch it off for deterministic due dates
STUDY_DAY_STARTS_AT = 4  # local hour


def scheduler(user: User) -> fsrs.Scheduler:
    # No learning/relearning steps: every interval is whole days.
    return fsrs.Scheduler(
        desired_retention=user.desired_retention, learning_steps=(), relearning_steps=(), enable_fuzzing=FUZZ
    )


def memory(card: Card) -> fsrs.Card:
    reviewed = card.last_reviewed_at is not None
    return fsrs.Card(
        card_id=card.id or 1,
        state=fsrs.State.Review if reviewed else fsrs.State.Learning,
        step=None if reviewed else 0,
        stability=card.stability,
        difficulty=card.difficulty,
        due=card.due_at,
        last_review=card.last_reviewed_at,
    )


def rating_for(correct: bool, confidence: Confidence) -> fsrs.Rating:
    if not correct:
        return fsrs.Rating.Again
    return {"confident": fsrs.Rating.Good, "educated_guess": fsrs.Rating.Hard}.get(confidence, fsrs.Rating.Again)


def study_day_start(user: User, now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo(user.timezone))
    start = local.replace(hour=STUDY_DAY_STARTS_AT, minute=0, second=0, microsecond=0)
    return start - timedelta(days=1) if local < start else start


def unstudied(user: User) -> ColumnElement[bool]:
    """Questions the user has never reviewed and not suspended."""
    return ~exists().where(
        Card.question_id == Question.id,
        Card.user_id == user.id,
        or_(Card.last_reviewed_at.is_not(None), Card.suspended_at.is_not(None)),
    )


def due_cards(user: User, now: datetime) -> Select[Card]:
    return select(Card).join(Question).where(
        Card.user_id == user.id,
        Card.suspended_at.is_(None),
        Card.last_reviewed_at.is_not(None),
        Card.due_at <= now,
    )


def new_remaining(db: Session, user: User, decks: dict[int, Deck], now: datetime) -> dict[int, int]:
    """New questions each deck may still introduce today; a deck's usage counts its whole subtree."""
    introduced = dict(
        db.execute(
            select(Question.deck_id, func.count())
            .join(Review, Review.question_id == Question.id)
            .where(
                Review.user_id == user.id,
                Review.was_new,
                Review.reviewed_at >= study_day_start(user, now),
                Question.deck_id.in_(decks),
            )
            .group_by(Question.deck_id)
        ).all()
    )
    return {
        deck_id: max(0, deck.new_per_day - sum(introduced.get(i, 0) for i in subtree_ids(decks, deck_id)))
        for deck_id, deck in decks.items()
    }


def admits(decks: dict[int, Deck], remaining: dict[int, int], deck_id: int, root_id: int) -> bool:
    """Anki v3 rule: every deck from the question's deck up to the session root needs allowance left."""
    current: int | None = deck_id
    while current is not None:
        if remaining[current] <= 0:
            return False
        if current == root_id:
            return True
        current = decks[current].parent_id
    return False  # not under root_id


def next_due(db: Session, user: User, deck_ids: list[int], now: datetime) -> Question | None:
    """Most forgotten (lowest retrievability) due card first."""
    sched = scheduler(user)
    # ponytail: ranks every due card in Python; move retrievability into SQL if due piles get huge.
    cards = db.scalars(due_cards(user, now).where(Question.deck_id.in_(deck_ids)))
    best = min(cards, key=lambda c: (sched.get_card_retrievability(memory(c), now), c.question_id), default=None)
    return db.get(Question, best.question_id) if best else None


def next_new(db: Session, user: User, decks: dict[int, Deck], root_id: int, now: datetime) -> Question | None:
    remaining = new_remaining(db, user, decks, now)
    if remaining[root_id] <= 0:
        return None
    # ponytail: scans unstudied question ids in order; fine for thousands of new questions.
    candidates = db.execute(
        select(Question.id, Question.deck_id)
        .where(Question.deck_id.in_(subtree_ids(decks, root_id)), unstudied(user))
        .order_by(Question.id)
    )
    for question_id, deck_id in candidates:
        if admits(decks, remaining, deck_id, root_id):
            return db.get(Question, question_id)
    return None


def start_session(db: Session, user: User, deck_id: int, size: int | None = None) -> dict[str, Any]:
    from app.services.stats import counts  # pylint: disable=import-outside-toplevel  # import cycle

    deck = get_deck(db, user, deck_id)
    session = StudySession(user_id=user.id, deck_id=deck.id, size=max(1, min(size or deck.session_size, 500)), answered=0)
    db.add(session)
    db.commit()
    decks = user_decks(db, user)
    available = counts(db, user, decks)[deck.id]
    return {
        "session_id": session.id,
        "size": session.size,
        "available": {"due": available["due"], "new": available["new"]},
        "recent_summaries": recent_summaries(db, user, decks),
    }


def recent_summaries(db: Session, user: User, decks: dict[int, Deck]) -> list[dict[str, Any]]:
    """The latest session summaries across all decks: general takeaways matter whichever deck comes next."""
    sessions = db.scalars(
        select(StudySession)
        .where(StudySession.user_id == user.id, StudySession.summary.is_not(None))
        .order_by(StudySession.id.desc())
        .limit(RECENT_SUMMARIES)
    )
    return [
        {"deck": " / ".join(path_names(decks, s.deck_id)), "started_at": s.created_at.isoformat(), "summary": s.summary}
        for s in sessions
    ]


def update_session(db: Session, user: User, session_id: int, data: SessionUpdate) -> dict[str, Any]:
    session = get_session(db, user, session_id)
    session.summary = data.summary or None
    db.commit()
    return {"session_id": session.id, "summary": session.summary}


def get_session(db: Session, user: User, session_id: int) -> StudySession:
    session = db.get(StudySession, session_id)
    if session is None or session.user_id != user.id:
        raise NotFound(f"Session {session_id} not found.")
    return session


def summary(db: Session, session: StudySession) -> dict[str, Any]:
    rows = db.execute(
        select(Review.confidence, func.count(), func.count().filter(Review.correct))
        .where(Review.study_session_id == session.id)
        .group_by(Review.confidence)
    ).all()
    by_confidence = {c: {"answered": 0, "correct": 0} for c in ("confident", "educated_guess", "complete_guess")}
    for confidence, answered, correct in rows:
        by_confidence[confidence] = {"answered": answered, "correct": correct}
    return {
        "answered": sum(r[1] for r in rows),
        "correct": sum(r[2] for r in rows),
        "misconceptions": by_confidence["confident"]["answered"] - by_confidence["confident"]["correct"],
        "by_confidence": by_confidence,
    }


def next_question(db: Session, user: User, session_id: int) -> dict[str, Any]:
    """Next question without its answer (options shuffled), or the summary once the session is done."""
    session = get_session(db, user, session_id)
    if session.finished_at is None and session.answered < session.size:
        now = datetime.now(UTC)
        decks = user_decks(db, user)
        question = next_due(db, user, subtree_ids(decks, session.deck_id), now) or next_new(
            db, user, decks, session.deck_id, now
        )
        if question:
            return {
                "finished": False,
                "session": {"id": session.id, "answered": session.answered, "size": session.size},
                "question": describe(question, with_answers=False, shuffle=True),
            }
    if session.finished_at is None:
        session.finished_at = datetime.now(UTC)
        db.commit()
    return {"finished": True, "summary": summary(db, session)}


def submit_answer(db: Session, user: User, session_id: int, answer: AnswerIn) -> dict[str, Any]:
    session = get_session(db, user, session_id)
    question = get_question(db, user, answer.question_id)
    if session.finished_at is not None or session.answered >= session.size:
        raise Invalid("session", "This session is already finished.")
    if question.deck_id not in subtree_ids(user_decks(db, user), session.deck_id):
        raise Invalid("question_id", "This question is not part of the session.")

    selected = list(dict.fromkeys(answer.selected))
    pick = QUESTION_SHAPES[question.type][1]
    if len(selected) != pick:
        raise Invalid("selected", f"Pick exactly {pick} option(s).")
    if not set(selected) <= {o["id"] for o in question.options}:
        raise Invalid("selected", "Unknown option id.")

    now = datetime.now(UTC)
    correct = set(selected) == {o["id"] for o in question.options if o["correct"]}
    rating = rating_for(correct, answer.confidence)
    card = card_for(db, user, question)
    was_new = card.last_reviewed_at is None
    reviewed, _ = scheduler(user).review_card(memory(card), rating, now)
    card.stability, card.difficulty = reviewed.stability, reviewed.difficulty
    card.due_at, card.last_reviewed_at = reviewed.due, now
    card.reps += 1
    card.lapses += rating == fsrs.Rating.Again and not was_new
    session.answered += 1
    db.add(
        Review(
            user_id=user.id,
            question_id=question.id,
            study_session_id=session.id,
            selected=selected,
            correct=correct,
            confidence=answer.confidence,
            rating=int(rating),
            was_new=was_new,
            reviewed_at=now,
        )
    )
    db.commit()
    return {
        "correct": correct,
        "misconception": not correct and answer.confidence == "confident",
        "rating": rating.name.lower(),
        "question": describe(question),
        "note": card.note,
        "next_review_at": reviewed.due.isoformat(),
        "session": {"id": session.id, "answered": session.answered, "size": session.size},
    }

"""Exams and their linked decks."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import Deck, DeckExam, Exam, User
from app.schemas import ExamCreate, ExamUpdate
from app.services import Invalid, NotFound
from app.services.content import subtree_ids, user_decks


def get_exam(db: Session, user: User, exam_id: int) -> Exam:
    exam = db.get(Exam, exam_id)
    if exam is None or exam.user_id != user.id:
        raise NotFound(f"Exam {exam_id} not found.")
    return exam


def exam_dict(exam: Exam) -> dict[str, Any]:
    decks = [{"id": link.deck_id, "name": link.deck.name} for link in exam.deck_exams]
    return {
        "id": exam.id,
        "name": exam.name,
        "starts_at": exam.starts_at.isoformat() if exam.starts_at else None,
        "completed_at": exam.completed_at.isoformat() if exam.completed_at else None,
        "created_at": exam.created_at.isoformat(),
        "updated_at": exam.updated_at.isoformat(),
        "deck_ids": [d["id"] for d in decks],
        "decks": decks,
    }


def list_exams(db: Session, user: User, upcoming: bool = False) -> list[Exam]:
    stmt = select(Exam).where(Exam.user_id == user.id)
    if upcoming:
        now = datetime.now(UTC)
        stmt = stmt.where(Exam.completed_at.is_(None), or_(Exam.starts_at.is_(None), Exam.starts_at >= now))
    return list(db.scalars(stmt.order_by(Exam.starts_at.asc().nulls_last(), Exam.name, Exam.id)).all())


def _owned_deck_ids(db: Session, user: User, deck_ids: list[int]) -> list[int]:
    """Return deck_ids if every id belongs to the user; otherwise NotFound (same as get_deck)."""
    if not deck_ids:
        return []
    found = set(db.scalars(select(Deck.id).where(Deck.id.in_(deck_ids), Deck.user_id == user.id)).all())
    missing = [deck_id for deck_id in deck_ids if deck_id not in found]
    if missing:
        raise NotFound(f"Deck {missing[0]} not found.")
    return deck_ids


def _reject_nested_links(db: Session, user: User, deck_ids: list[int]) -> None:
    """Linking both a parent and a child is redundant (subtree already includes the child)."""
    if len(deck_ids) < 2:
        return
    decks = user_decks(db, user)
    linked = set(deck_ids)
    for deck_id in deck_ids:
        current = decks[deck_id].parent_id
        while current is not None:
            if current in linked:
                raise Invalid(
                    "deck_ids",
                    f"Deck {deck_id} is under linked deck {current}; link only the parent.",
                )
            current = decks[current].parent_id


def _replace_deck_links(db: Session, user: User, exam: Exam, deck_ids: list[int]) -> None:
    owned = _owned_deck_ids(db, user, deck_ids)
    _reject_nested_links(db, user, owned)
    db.execute(delete(DeckExam).where(DeckExam.exam_id == exam.id))
    if owned:
        db.add_all([DeckExam(exam_id=exam.id, deck_id=deck_id) for deck_id in owned])


def create_exam(db: Session, user: User, data: ExamCreate) -> Exam:
    exam = Exam(user_id=user.id, name=data.name, starts_at=data.starts_at)
    db.add(exam)
    db.flush()
    if data.deck_ids is not None:
        _replace_deck_links(db, user, exam, data.deck_ids)
    db.commit()
    db.refresh(exam)
    return exam


def update_exam(db: Session, user: User, exam_id: int, data: ExamUpdate) -> Exam:
    exam = get_exam(db, user, exam_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise Invalid("name", "Cannot be null.")
    deck_ids = changes.pop("deck_ids", None)
    completed = changes.pop("completed", None)
    for field, value in changes.items():
        setattr(exam, field, value)
    if completed is True:
        exam.completed_at = datetime.now(UTC)
    elif completed is False:
        exam.completed_at = None
    if deck_ids is not None:
        _replace_deck_links(db, user, exam, deck_ids)
    db.commit()
    db.refresh(exam)
    return exam


def delete_exam(db: Session, user: User, exam_id: int) -> None:
    db.delete(get_exam(db, user, exam_id))
    db.commit()


def exam_scope(db: Session, user: User, exam_id: int) -> tuple[Exam, list[int], list[int]]:
    """Exam, linked root deck ids, and the union of their subtree deck ids (for stats)."""
    exam = get_exam(db, user, exam_id)
    decks = user_decks(db, user)
    roots = [link.deck_id for link in exam.deck_exams]
    scope: list[int] = []
    seen: set[int] = set()
    for root_id in roots:
        for deck_id in subtree_ids(decks, root_id):
            if deck_id not in seen:
                seen.add(deck_id)
                scope.append(deck_id)
    return exam, roots, scope

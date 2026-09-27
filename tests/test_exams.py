from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_deck, make_questions
from sqlalchemy.orm import Session

from app.models import DeckExam, User
from app.schemas import ExamCreate, ExamUpdate
from app.services import Invalid, NotFound, stats
from app.services.exams import create_exam, delete_exam, exam_dict, get_exam, list_exams, update_exam


def test_create_exam(db: Session, user: User) -> None:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    exam = create_exam(db, user, ExamCreate(name="Test Exam", starts_at=starts_at))
    assert exam.id is not None
    assert exam.name == "Test Exam"
    assert exam.starts_at == starts_at
    assert exam.deck_exams == []
    assert exam.completed_at is None


def test_create_exam_with_decks(db: Session, user: User) -> None:
    a = make_deck(db, user, "A")
    b = make_deck(db, user, "B")
    exam = create_exam(db, user, ExamCreate(name="Linked", deck_ids=[b.id, a.id, a.id]))
    body = exam_dict(exam)
    assert body["deck_ids"] == sorted([a.id, b.id])
    assert {d["name"] for d in body["decks"]} == {"A", "B"}


def test_create_rejects_other_users_deck(db: Session, user: User, other: User) -> None:
    theirs = make_deck(db, other, "Theirs")
    with pytest.raises(NotFound):
        create_exam(db, user, ExamCreate(name="Nope", deck_ids=[theirs.id]))


def test_create_rejects_parent_and_child_links(db: Session, user: User) -> None:
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    with pytest.raises(Invalid):
        create_exam(db, user, ExamCreate(name="Nested", deck_ids=[root.id, child.id]))


def test_list_exams_ordered_by_starts_at(db: Session, user: User) -> None:
    later = datetime.now(UTC) + timedelta(days=3)
    sooner = datetime.now(UTC) + timedelta(days=1)
    create_exam(db, user, ExamCreate(name="Later", starts_at=later))
    create_exam(db, user, ExamCreate(name="Sooner", starts_at=sooner))
    create_exam(db, user, ExamCreate(name="Undated", starts_at=None))
    assert [e.name for e in list_exams(db, user)] == ["Sooner", "Later", "Undated"]


def test_list_exams_upcoming_filter(db: Session, user: User) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)
    create_exam(db, user, ExamCreate(name="Past", starts_at=past))
    create_exam(db, user, ExamCreate(name="Future", starts_at=future))
    undated = create_exam(db, user, ExamCreate(name="Undated"))
    update_exam(db, user, undated.id, ExamUpdate(completed=True))
    create_exam(db, user, ExamCreate(name="Open"))
    assert {e.name for e in list_exams(db, user, upcoming=True)} == {"Future", "Open"}


def test_get_exam(db: Session, user: User) -> None:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    created = create_exam(db, user, ExamCreate(name="Test Exam", starts_at=starts_at))
    exam = get_exam(db, user, created.id)
    assert exam.id == created.id
    assert exam.name == "Test Exam"
    assert exam.starts_at == starts_at


def test_get_exam_hides_others(db: Session, user: User, other: User) -> None:
    exam = create_exam(db, other, ExamCreate(name="Bob's"))
    with pytest.raises(NotFound):
        get_exam(db, user, exam.id)


def test_update_exam(db: Session, user: User) -> None:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    exam = create_exam(db, user, ExamCreate(name="Test Exam", starts_at=starts_at))
    update_exam(db, user, exam.id, ExamUpdate(name="Updated Exam", starts_at=starts_at + timedelta(days=2)))
    exam = get_exam(db, user, exam.id)
    assert exam.name == "Updated Exam"
    assert exam.starts_at == starts_at + timedelta(days=2)


def test_update_clears_starts_at_and_toggles_completed(db: Session, user: User) -> None:
    starts_at = datetime.now(UTC) + timedelta(days=1)
    exam = create_exam(db, user, ExamCreate(name="Exam", starts_at=starts_at))
    update_exam(db, user, exam.id, ExamUpdate(starts_at=None))
    assert get_exam(db, user, exam.id).starts_at is None
    update_exam(db, user, exam.id, ExamUpdate(completed=True))
    assert get_exam(db, user, exam.id).completed_at is not None
    update_exam(db, user, exam.id, ExamUpdate(completed=False))
    assert get_exam(db, user, exam.id).completed_at is None


def test_update_deck_ids_replaces_and_clear_leaves_name(db: Session, user: User) -> None:
    a = make_deck(db, user, "A")
    b = make_deck(db, user, "B")
    exam = create_exam(db, user, ExamCreate(name="Exam", deck_ids=[a.id]))
    update_exam(db, user, exam.id, ExamUpdate(deck_ids=[b.id]))
    assert exam_dict(get_exam(db, user, exam.id))["deck_ids"] == [b.id]
    update_exam(db, user, exam.id, ExamUpdate(name="Renamed"))
    got = get_exam(db, user, exam.id)
    assert got.name == "Renamed"
    assert exam_dict(got)["deck_ids"] == [b.id]
    update_exam(db, user, exam.id, ExamUpdate(deck_ids=[]))
    assert exam_dict(get_exam(db, user, exam.id))["deck_ids"] == []


def test_update_rejects_null_name(db: Session, user: User) -> None:
    exam = create_exam(db, user, ExamCreate(name="Exam"))
    with pytest.raises(Invalid):
        update_exam(db, user, exam.id, ExamUpdate(name=None))


def test_delete_exam(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    exam = create_exam(db, user, ExamCreate(name="Test Exam", deck_ids=[deck.id]))
    exam_id = exam.id
    delete_exam(db, user, exam_id)
    with pytest.raises(NotFound):
        get_exam(db, user, exam_id)
    assert db.query(DeckExam).filter_by(exam_id=exam_id).count() == 0


def test_performance_by_exam_id(db: Session, user: User) -> None:
    deck = make_deck(db, user, "IAM")
    make_questions(db, user, deck, 2)
    other = make_deck(db, user, "Other")
    make_questions(db, user, other, 3)
    starts_at = datetime.now(UTC) + timedelta(days=7)
    exam = create_exam(db, user, ExamCreate(name="SAA", starts_at=starts_at, deck_ids=[deck.id]))
    report = stats.performance(db, user, exam_id=exam.id)
    assert report["counts"]["total"] == 2
    assert report["readiness"]["questions"] == 2
    assert report["readiness"]["by_deck"][0]["name"] == "IAM"
    with pytest.raises(Invalid):
        stats.performance(db, user, deck_id=deck.id, exam_id=exam.id)

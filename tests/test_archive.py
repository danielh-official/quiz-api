"""Deck archive / unarchive."""

import pytest
from conftest import make_deck, make_questions, question_in
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import AnswerIn, DeckCreate, DeckUpdate, ExamCreate, QuestionUpdate
from app.services import Invalid, content, exams, stats, study


def test_archive_hides_from_default_list(db: Session, user: User) -> None:
    keep = make_deck(db, user, "Keep")
    hide = make_deck(db, user, "Hide")
    content.update_deck(db, user, hide.id, DeckUpdate(archived=True))

    active = content.list_decks(db, user)
    assert [d["name"] for d in active] == ["Keep"]
    assert active[0]["archived"] is False

    archived = content.list_decks(db, user, archived=True)
    assert [d["name"] for d in archived] == ["Hide"]
    assert archived[0]["archived"] is True
    assert archived[0]["archived_at"] is not None
    assert keep.id == content.get_deck(db, user, keep.id).id


def test_archive_parent_hides_subtree_unarchive_keeps_direct_child(db: Session, user: User) -> None:
    parent = make_deck(db, user, "Parent")
    child = make_deck(db, user, "Child", parent_id=parent.id)
    grand = make_deck(db, user, "Grand", parent_id=child.id)
    content.update_deck(db, user, child.id, DeckUpdate(archived=True))
    content.update_deck(db, user, parent.id, DeckUpdate(archived=True))

    assert not content.list_decks(db, user)
    names = {d["name"] for d in content.list_decks(db, user, archived=True)}
    assert names == {"Parent", "Child"}  # grand has no direct stamp

    content.update_deck(db, user, parent.id, DeckUpdate(archived=False))
    active = content.list_decks(db, user)
    assert [d["name"] for d in active] == ["Parent"]
    archived = content.list_decks(db, user, archived=True)
    assert [d["name"] for d in archived] == ["Child"]
    assert content.get_deck(db, user, grand.id).archived_at is None


def test_unarchive_with_rename_same_request(db: Session, user: User) -> None:
    deck = make_deck(db, user, "Old")
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    updated = content.update_deck(db, user, deck.id, DeckUpdate(archived=False, name="New"))
    assert updated.name == "New"
    assert updated.archived_at is None


def test_cannot_edit_while_archived(db: Session, user: User) -> None:
    deck = make_deck(db, user, "D")
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    with pytest.raises(Invalid):
        content.update_deck(db, user, deck.id, DeckUpdate(name="Nope"))
    with pytest.raises(Invalid):
        content.update_deck(db, user, deck.id, DeckUpdate(archived=True, name="Nope"))


def test_cannot_create_under_archived_or_add_questions(db: Session, user: User) -> None:
    parent = make_deck(db, user, "P")
    content.update_deck(db, user, parent.id, DeckUpdate(archived=True))
    with pytest.raises(Invalid):
        content.create_deck(db, user, DeckCreate(name="Child", parent_id=parent.id))
    with pytest.raises(Invalid):
        content.create_questions(db, user, parent.id, [question_in()])


def test_counts_exclude_archived_child(db: Session, user: User) -> None:
    parent = make_deck(db, user, "P")
    child = make_deck(db, user, "C", parent_id=parent.id)
    make_questions(db, user, parent, 1)
    make_questions(db, user, child, 2)
    content.update_deck(db, user, child.id, DeckUpdate(archived=True))

    listed = {d["name"]: d for d in content.list_decks(db, user)}
    assert listed["P"]["total"] == 1
    detail = content.deck_detail(db, user, parent.id)
    assert detail["deck"]["total"] == 3  # full browse includes archived branch


def test_search_archive_filter(db: Session, user: User) -> None:
    active = make_deck(db, user, "Active")
    retired = make_deck(db, user, "Retired")
    content.create_questions(db, user, active.id, [question_in("alpha unique")])
    content.create_questions(db, user, retired.id, [question_in("alpha unique")])
    content.update_deck(db, user, retired.id, DeckUpdate(archived=True))

    assert len(content.search_questions(db, user, "alpha")["questions"]) == 1
    assert len(content.search_questions(db, user, "alpha", archived="archived")["questions"]) == 1
    assert len(content.search_questions(db, user, "alpha", archived="all")["questions"]) == 2


def test_study_blocked_on_archived(db: Session, user: User) -> None:
    deck = make_deck(db, user, "D")
    make_questions(db, user, deck, 1)
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    with pytest.raises(Invalid):
        study.start_session(db, user, deck.id)


def test_open_session_fails_after_archive(db: Session, user: User) -> None:
    deck = make_deck(db, user, "D")
    [q] = make_questions(db, user, deck, 1)
    started = study.start_session(db, user, deck.id, size=1)
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    with pytest.raises(Invalid):
        study.next_question(db, user, started["session_id"])
    with pytest.raises(Invalid):
        study.submit_answer(
            db,
            user,
            started["session_id"],
            AnswerIn(question_id=q.id, selected=[q.options[0]["id"]], confidence="confident"),
        )


def test_exam_rejects_archived_link_and_scope_skips(db: Session, user: User) -> None:
    active = make_deck(db, user, "Active")
    retired = make_deck(db, user, "Retired")
    make_questions(db, user, active, 1)
    make_questions(db, user, retired, 1)
    exam = exams.create_exam(db, user, ExamCreate(name="E", deck_ids=[active.id, retired.id]))
    content.update_deck(db, user, retired.id, DeckUpdate(archived=True))
    _, roots, scope = exams.exam_scope(db, user, exam.id)
    assert roots == [active.id]
    assert set(scope) == {active.id}

    with pytest.raises(Invalid):
        exams.create_exam(db, user, ExamCreate(name="Bad", deck_ids=[retired.id]))


def test_performance_all_excludes_archived(db: Session, user: User) -> None:
    active = make_deck(db, user, "Active")
    retired = make_deck(db, user, "Retired")
    make_questions(db, user, active, 1)
    make_questions(db, user, retired, 2)
    content.update_deck(db, user, retired.id, DeckUpdate(archived=True))
    assert stats.performance(db, user)["counts"]["total"] == 1
    assert stats.performance(db, user, deck_id=retired.id)["counts"]["total"] == 2


def test_delete_archived_allowed(db: Session, user: User) -> None:
    deck = make_deck(db, user, "D")
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    content.delete_deck(db, user, deck.id)
    assert not content.list_decks(db, user, archived=True)


def test_stamp_under_archived_ancestor(db: Session, user: User) -> None:
    parent = make_deck(db, user, "P")
    child = make_deck(db, user, "C", parent_id=parent.id)
    content.update_deck(db, user, parent.id, DeckUpdate(archived=True))
    content.update_deck(db, user, child.id, DeckUpdate(archived=True))
    content.update_deck(db, user, parent.id, DeckUpdate(archived=False))
    assert [d["name"] for d in content.list_decks(db, user)] == ["P"]
    assert [d["name"] for d in content.list_decks(db, user, archived=True)] == ["C"]


def test_update_question_blocked_when_archived(db: Session, user: User) -> None:
    deck = make_deck(db, user, "D")
    q = make_questions(db, user, deck, 1)[0]
    content.update_deck(db, user, deck.id, DeckUpdate(archived=True))
    with pytest.raises(Invalid):
        content.update_question(db, user, q.id, QuestionUpdate(stem="changed"))

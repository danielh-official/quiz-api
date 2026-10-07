from datetime import UTC, datetime

import pytest
from conftest import make_deck, make_questions

from sqlalchemy.orm import Session

from app.models import Deck, Question, Review, User
from app.schemas import DeckUpdate
from app.services import Invalid, NotFound, content


def test_nested_decks_list_depth_first(db: Session, user: User) -> None:
    root = make_deck(db, user, "AWS")
    s3 = make_deck(db, user, "S3", parent_id=root.id)
    make_deck(db, user, "Glacier", parent_id=s3.id)
    make_deck(db, user, "IAM", parent_id=root.id)

    listed = [(d["name"], d["depth"]) for d in content.list_decks(db, user)]
    assert listed == [("AWS", 0), ("IAM", 1), ("S3", 1), ("Glacier", 2)]


def test_deck_cannot_move_under_its_own_subtree(db: Session, user: User) -> None:
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    with pytest.raises(Invalid):
        content.update_deck(db, user, root.id, DeckUpdate(parent_id=child.id))
    with pytest.raises(Invalid):
        content.update_deck(db, user, root.id, DeckUpdate(parent_id=root.id))


def test_parent_must_be_own_deck(db: Session, user: User, other: User) -> None:
    theirs = make_deck(db, other, "Theirs")
    with pytest.raises(Invalid):
        make_deck(db, user, "Mine", parent_id=theirs.id)
    with pytest.raises(NotFound):
        content.get_deck(db, user, theirs.id)


def test_move_to_top_level(db: Session, user: User) -> None:
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    assert content.update_deck(db, user, child.id, DeckUpdate(parent_id=None)).parent_id is None


def test_delete_cascades_to_subdecks_and_questions(db: Session, user: User) -> None:
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    make_questions(db, user, child, 2)
    content.delete_deck(db, user, root.id)
    assert db.query(Deck).count() == 0
    assert db.query(Question).count() == 0


def test_deck_questions_sort_last_answered_first(db: Session, user: User, other: User) -> None:
    deck = make_deck(db, user, "D")
    never, old, recent = make_questions(db, user, deck, n=3)

    def review(question: Question, day: int, who: User = user) -> None:
        when = datetime(2026, 1, day, tzinfo=UTC)
        db.add(
            Review(
                user_id=who.id,
                question_id=question.id,
                selected=[],
                correct=True,
                confidence="confident",
                rating=3,
                was_new=False,
                reviewed_at=when,
            )
        )

    review(old, 1)
    review(recent, 2)
    review(never, 3, who=other)  # someone else's answer doesn't count
    db.flush()
    assert [q["id"] for q in content.deck_detail(db, user, deck.id, recent_first=True)["questions"]] == [
        recent.id,
        old.id,
        never.id,
    ]
    assert [q["id"] for q in content.deck_detail(db, user, deck.id)["questions"]] == [never.id, old.id, recent.id]


def test_deck_detail_subdecks_carry_counts(db: Session, user: User) -> None:
    parent = make_deck(db, user, "Parent")
    child = make_deck(db, user, "Child", parent_id=parent.id, new_per_day=1)
    grandchild = make_deck(db, user, "Grandchild", parent_id=child.id)
    make_questions(db, user, child, n=2)
    make_questions(db, user, grandchild, n=1)

    (sub,) = content.deck_detail(db, user, parent.id)["subdecks"]
    assert (sub["name"], sub["due"], sub["new"], sub["total"]) == ("Child", 0, 1, 3)

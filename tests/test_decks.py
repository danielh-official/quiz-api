import pytest
from conftest import make_deck, make_questions

from app.models import Deck, Question
from app.schemas import DeckUpdate
from app.services import Invalid, NotFound, content


def test_nested_decks_list_depth_first(db, user):
    root = make_deck(db, user, "AWS")
    s3 = make_deck(db, user, "S3", parent_id=root.id)
    make_deck(db, user, "Glacier", parent_id=s3.id)
    make_deck(db, user, "IAM", parent_id=root.id)

    listed = [(d["name"], d["depth"]) for d in content.list_decks(db, user)]
    assert listed == [("AWS", 0), ("IAM", 1), ("S3", 1), ("Glacier", 2)]


def test_deck_cannot_move_under_its_own_subtree(db, user):
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    with pytest.raises(Invalid):
        content.update_deck(db, user, root.id, DeckUpdate(parent_id=child.id))
    with pytest.raises(Invalid):
        content.update_deck(db, user, root.id, DeckUpdate(parent_id=root.id))


def test_parent_must_be_own_deck(db, user, other):
    theirs = make_deck(db, other, "Theirs")
    with pytest.raises(Invalid):
        make_deck(db, user, "Mine", parent_id=theirs.id)
    with pytest.raises(NotFound):
        content.get_deck(db, user, theirs.id)


def test_move_to_top_level(db, user):
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    assert content.update_deck(db, user, child.id, DeckUpdate(parent_id=None)).parent_id is None


def test_delete_cascades_to_subdecks_and_questions(db, user):
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    make_questions(db, user, child, 2)
    content.delete_deck(db, user, root.id)
    assert db.query(Deck).count() == 0
    assert db.query(Question).count() == 0

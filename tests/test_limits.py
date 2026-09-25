"""new_per_day follows Anki v3: every deck between the question's deck and the session root caps new cards."""

from conftest import answer, make_deck, make_questions

from sqlalchemy.orm import Session

from app.models import Deck, User
from app.services import content, study
from app.services.stats import counts


def tree(db: Session, user: User, root_limit: int = 10, s_limit: int = 2, t_limit: int = 1) -> tuple[Deck, Deck, Deck]:
    root = make_deck(db, user, "R", new_per_day=root_limit)
    s = make_deck(db, user, "S", parent_id=root.id, new_per_day=s_limit)
    t = make_deck(db, user, "T", parent_id=s.id, new_per_day=t_limit)
    for deck in (root, s, t):
        make_questions(db, user, deck, 3, prefix=deck.name)
    return root, s, t


def study_all(db: Session, user: User, deck: Deck) -> list[int]:
    """Deck ids of every question served in one big session."""
    session = study.start_session(db, user, deck.id, 50)["session_id"]
    served = []
    while not (nxt := study.next_question(db, user, session))["finished"]:
        q = content.get_question(db, user, nxt["question"]["id"])
        served.append(q.deck_id)
        answer(db, user, session, q)
    return served


def test_subdeck_limits_cap_their_subtree(db: Session, user: User) -> None:
    root, s, t = tree(db, user)
    assert counts(db, user, content.user_decks(db, user))[root.id]["new"] == 5

    served = study_all(db, user, root)
    assert len(served) == 5
    assert served.count(t.id) <= 1
    assert served.count(s.id) + served.count(t.id) == 2


def test_own_limit_caps_deck_even_with_parent_room(db: Session, user: User) -> None:
    root = make_deck(db, user, "R", new_per_day=10)
    t = make_deck(db, user, "T", parent_id=root.id, new_per_day=1)
    make_questions(db, user, t, 3, prefix="T")
    make_questions(db, user, root, 3, prefix="R")  # higher ids than T's questions
    served = study_all(db, user, root)
    assert served.count(t.id) == 1 and served.count(root.id) == 3


def test_parent_limit_caps_children(db: Session, user: User) -> None:
    root, _, _ = tree(db, user, root_limit=1, s_limit=5, t_limit=5)
    assert len(study_all(db, user, root)) == 1


def test_limits_above_session_root_ignored(db: Session, user: User) -> None:
    _, s, t = tree(db, user, root_limit=0)
    served = study_all(db, user, s)
    assert len(served) == 2 and served.count(t.id) <= 1
    assert counts(db, user, content.user_decks(db, user))[s.id]["new"] == 0  # used up today

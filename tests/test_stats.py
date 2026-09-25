from conftest import answer, make_deck, make_questions

from app.schemas import CardUpdate
from app.services import content, stats, study


def test_counts_roll_up_and_skip_suspended(db, user):
    root = make_deck(db, user, "Root", new_per_day=100)
    child = make_deck(db, user, "Child", parent_id=root.id)
    make_questions(db, user, root, 2, prefix="R")
    suspended, _ = make_questions(db, user, child, 2, prefix="C")
    content.update_card(db, user, suspended.id, CardUpdate(suspended=True))

    per = stats.counts(db, user, content.user_decks(db, user))
    assert per[root.id] == {"due": 0, "new": 3, "total": 4}
    assert per[child.id] == {"due": 0, "new": 1, "total": 2}


def test_new_count_capped_by_daily_limit(db, user):
    deck = make_deck(db, user, new_per_day=2)
    make_questions(db, user, deck, 5)
    assert stats.counts(db, user, content.user_decks(db, user))[deck.id]["new"] == 2


def test_performance_reports_misconceptions_leeches_notes(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    for _ in range(2):
        answer(db, user, study.start_session(db, user, deck.id)["session_id"], q, right=False)
    content.update_card(db, user, q.id, CardUpdate(note="remember X"))

    report = stats.performance(db, user, deck.id)
    assert report["calibration"]["confident"] == {"answered": 2, "correct": 0}
    assert [m["question"]["id"] for m in report["misconceptions"]] == [q.id]
    assert report["leeches"][0]["lapses"] == 1
    assert report["notes"] == [{"question_id": q.id, "stem": q.stem, "note": "remember X"}]


def test_users_are_isolated(db, user, other):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    answer(db, user, study.start_session(db, user, deck.id)["session_id"], q)

    assert stats.performance(db, other)["counts"] == {"due": 0, "new": 0, "total": 0}
    assert content.list_decks(db, other) == []

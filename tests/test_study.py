from datetime import UTC, datetime, timedelta

import pytest
from conftest import answer, make_deck, make_questions

from app.models import Card, Review
from app.schemas import AnswerIn, CardUpdate, QuestionUpdate
from app.services import Invalid, NotFound, content, study


def start(db, user, deck, size=None):
    return study.start_session(db, user, deck.id, size)["session_id"]


def test_rating_from_confidence():
    assert study.rating_for(False, "confident").name == "Again"
    assert study.rating_for(True, "confident").name == "Good"
    assert study.rating_for(True, "educated_guess").name == "Hard"
    assert study.rating_for(True, "complete_guess").name == "Again"


def test_answer_creates_card_and_review(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    result = answer(db, user, start(db, user, deck), q)

    card = db.query(Card).one()
    assert result["correct"] and result["rating"] == "good"
    assert card.reps == 1 and card.lapses == 0 and card.due_at > datetime.now(UTC) + timedelta(hours=12)
    assert db.query(Review).one().was_new


def test_confident_wrong_on_reviewed_card_is_a_lapse(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    answer(db, user, start(db, user, deck), q, right=False)
    assert db.query(Card).one().lapses == 0  # new cards don't lapse

    result = answer(db, user, start(db, user, deck), q, right=False)
    assert result["misconception"]
    assert db.query(Card).one().lapses == 1


def make_due(db, user, question, stability, days_overdue=1):
    now = datetime.now(UTC)
    db.add(
        Card(
            user_id=user.id,
            question_id=question.id,
            stability=stability,
            difficulty=5.0,
            due_at=now - timedelta(days=days_overdue),
            last_reviewed_at=now - timedelta(days=10),
            reps=1,
            lapses=0,
        )
    )
    db.commit()


def test_due_before_new_most_forgotten_first(db, user):
    deck = make_deck(db, user)
    new, strong, weak = make_questions(db, user, deck, 3)
    make_due(db, user, strong, stability=30)
    make_due(db, user, weak, stability=2)
    session = start(db, user, deck)

    order = []
    for _ in range(3):
        nxt = study.next_question(db, user, session)
        q = content.get_question(db, user, nxt["question"]["id"])
        order.append(q.id)
        answer(db, user, session, q)
    assert order == [weak.id, strong.id, new.id]


def test_subdecks_included_suspended_skipped(db, user):
    root = make_deck(db, user, "Root")
    child = make_deck(db, user, "Child", parent_id=root.id)
    [skip] = make_questions(db, user, root, prefix="R")
    [q] = make_questions(db, user, child, prefix="C")
    content.update_card(db, user, skip.id, CardUpdate(suspended=True))

    session = start(db, user, root)
    assert study.next_question(db, user, session)["question"]["id"] == q.id
    answer(db, user, session, q)
    assert study.next_question(db, user, session)["finished"]


def test_new_allowance_resets_at_4am_local(db, user):
    user.timezone = "America/New_York"
    deck = make_deck(db, user, new_per_day=1)
    old, q = make_questions(db, user, deck, 2)
    day_start = study.study_day_start(user, datetime.now(UTC))
    db.add(Review(user_id=user.id, question_id=old.id, selected=[], correct=True, confidence="confident", rating=3, was_new=True, reviewed_at=day_start - timedelta(minutes=1)))
    db.commit()
    assert study.next_question(db, user, start(db, user, deck))["question"]["id"] in (old.id, q.id)

    db.add(Review(user_id=user.id, question_id=old.id, selected=[], correct=True, confidence="confident", rating=3, was_new=True, reviewed_at=day_start + timedelta(seconds=1)))
    db.commit()
    assert study.next_question(db, user, start(db, user, deck))["finished"]


def test_session_size_caps_and_summary_groups_by_confidence(db, user):
    deck = make_deck(db, user)
    qs = make_questions(db, user, deck, 3)
    session = start(db, user, deck, size=2)
    answer(db, user, session, qs[0], confidence="confident")
    answer(db, user, session, qs[1], right=False, confidence="complete_guess")

    done = study.next_question(db, user, session)
    assert done["finished"]
    assert done["summary"]["answered"] == 2 and done["summary"]["correct"] == 1
    assert done["summary"]["by_confidence"]["complete_guess"] == {"answered": 1, "correct": 0}
    with pytest.raises(Invalid, match="finished"):
        answer(db, user, session, qs[2])


def test_next_question_hides_answers(db, user):
    deck = make_deck(db, user)
    make_questions(db, user, deck, type="select_two")
    q = study.next_question(db, user, start(db, user, deck))["question"]
    assert q["pick"] == 2 and "explanation" not in q
    assert all(set(o) == {"id", "text"} for o in q["options"])


def test_select_two_needs_two_picks(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck, type="select_two")
    with pytest.raises(Invalid, match="exactly 2"):
        study.submit_answer(
            db, user, start(db, user, deck), AnswerIn(question_id=q.id, selected=[q.options[0]["id"]], confidence="confident")
        )


def test_foreign_sessions_and_questions_rejected(db, user, other):
    deck = make_deck(db, user)
    elsewhere = make_deck(db, user, "Elsewhere")
    [outside] = make_questions(db, user, elsewhere)
    session = start(db, user, deck)
    with pytest.raises(Invalid, match="not part"):
        answer(db, user, session, outside)
    with pytest.raises(NotFound):
        study.next_question(db, other, session)


def test_reset_progress_keeps_note_and_suspension(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    answer(db, user, start(db, user, deck), q)
    content.update_card(db, user, q.id, CardUpdate(note="mine", suspended=True))
    content.update_question(db, user, q.id, QuestionUpdate(reset_progress=True))

    card = db.query(Card).one()
    db.refresh(card)
    assert card.last_reviewed_at is None and card.reps == 0
    assert card.note == "mine" and card.suspended_at is not None


def test_next_question_puts_answer_in_every_position(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    correct = next(o["id"] for o in q.options if o["correct"])
    session = start(db, user, deck)
    seen = {
        [o["id"] for o in study.next_question(db, user, session)["question"]["options"]].index(correct)
        for _ in range(200)
    }
    assert seen == {0, 1, 2, 3}

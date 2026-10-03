from typing import Any

import pytest
from conftest import answer, make_deck, make_questions, question_in
from pydantic import ValidationError

from sqlalchemy.orm import Session

from app.models import Review, User
from app.schemas import AnswerIn, CardUpdate, OptionIn, QuestionIn, QuestionType, QuestionUpdate
from app.services import NotFound, content, study


@pytest.mark.parametrize(
    ("type", "count", "correct", "error"),
    [
        ("single", 3, [0], "exactly 4 options"),
        ("single", 4, [0, 1], "exactly 1 correct"),
        ("select_two", 5, [0], "exactly 2 correct"),
        ("select_two", 4, [0, 1], "exactly 5 options"),
    ],
)
def test_question_shape_is_enforced(type: QuestionType, count: int, correct: list[int], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        QuestionIn(type=type, stem="S", options=[OptionIn(text=f"o{i}", correct=i in correct) for i in range(count)])


def test_option_texts_must_be_distinct() -> None:
    options = [OptionIn(text="Same", correct=True)] + [OptionIn(text=t) for t in ("same ", "b", "c")]
    with pytest.raises(ValidationError, match="distinct"):
        QuestionIn(type="single", stem="S", options=options)


def test_option_ids_survive_reordering_and_edits(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    stored = [o["id"] for o in q.options]
    reordered = [OptionIn(**o) for o in reversed(q.options)]
    reordered[0].text = "Edited text"  # keeps its id
    reordered[1] = OptionIn(text="Brand new", correct=reordered[1].correct)  # no id -> fresh id

    updated = content.update_question(db, user, q.id, QuestionUpdate(options=reordered))

    ids = [o["id"] for o in updated.options]
    assert ids[0] == stored[3]
    assert ids[1] not in stored
    assert ids[2:] == [stored[1], stored[0]]
    assert len(set(ids)) == 4


def test_deck_detail_hides_suspended_unless_requested(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    active, paused = make_questions(db, user, deck, n=2, prefix="Q")
    content.update_card(db, user, paused.id, CardUpdate(suspended=True))

    detail = content.deck_detail(db, user, deck.id)
    assert [q["id"] for q in detail["questions"]] == [active.id]
    assert detail["deck"]["suspended_count"] == 1

    suspended = content.deck_detail(db, user, deck.id, suspended=True)
    assert [q["id"] for q in suspended["questions"]] == [paused.id]
    assert suspended["suspended"] is True


def test_deck_detail_includes_answer_stats(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    single, dual = (
        make_questions(db, user, deck, n=1, prefix="S")[0],
        make_questions(db, user, deck, n=1, prefix="D", type="select_two")[0],
    )
    session = study.start_session(db, user, deck.id, size=2)["session_id"]
    answer(db, user, session, single)
    answer(db, user, session, dual, right=False)

    detail = content.deck_detail(db, user, deck.id)
    by_id = {q["id"]: q for q in detail["questions"]}
    assert by_id[single.id]["type"] == "single"
    assert by_id[single.id]["answered"] == 1
    assert by_id[single.id]["correct"] == 1
    assert by_id[single.id]["last_answered_at"] is not None
    assert by_id[dual.id]["type"] == "select_two"
    assert by_id[dual.id]["answered"] == 1
    assert by_id[dual.id]["correct"] == 0


def test_reviews_keep_meaning_after_reorder(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    session = study.start_session(db, user, deck.id)
    answer(db, user, session["session_id"], q)
    content.update_question(db, user, q.id, QuestionUpdate(options=[OptionIn(**o) for o in reversed(q.options)]))

    review = db.query(Review).one()
    picked = next(o for o in q.options if o["id"] == review.selected[0])
    assert picked["correct"]


def test_grading_is_exact_match(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    q = content.create_questions(db, user, deck.id, [question_in("S2", "select_two")])[0]
    session = study.start_session(db, user, deck.id)
    right = next(o["id"] for o in q.options if o["correct"])
    wrong = next(o["id"] for o in q.options if not o["correct"])
    one_right_one_wrong = [right, wrong]

    result = study.submit_answer(
        db, user, session["session_id"], AnswerIn(question_id=q.id, selected=one_right_one_wrong, confidence="confident")
    )
    assert result["correct"] is False
    assert result["misconception"] is True


def test_partial_update_keeps_other_fields(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    updated = content.update_question(db, user, q.id, QuestionUpdate(stem="New stem"))
    assert updated.stem == "New stem"
    assert updated.explanation == "because"
    assert len(updated.options) == 4


def test_update_is_revalidated(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    with pytest.raises(ValidationError, match="exactly 5 options"):
        content.update_question(db, user, q.id, QuestionUpdate(type="select_two"))


def test_questions_move_only_to_own_decks(db: Session, user: User, other: User) -> None:
    deck = make_deck(db, user)
    theirs = make_deck(db, other, "Theirs")
    [q] = make_questions(db, user, deck)
    with pytest.raises(NotFound):
        content.update_question(db, user, q.id, QuestionUpdate(deck_id=theirs.id))
    with pytest.raises(NotFound):
        content.get_question(db, other, q.id)


def test_search_matches_option_text_not_json_keys(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    make_questions(db, user, deck, 2)
    assert len(content.search_questions(db, user, "Q1 option")["questions"]) == 1
    assert content.search_questions(db, user, "correct")["questions"] == []


def correct_positions(options: list[dict[str, Any]]) -> set[int]:
    return {i for i, o in enumerate(options) if o["correct"]}


def test_stored_option_order_is_shuffled(db: Session, user: User) -> None:
    deck = make_deck(db, user)
    questions = make_questions(db, user, deck, 40)  # authored with the answer always first
    assert len({min(correct_positions(q.options)) for q in questions}) > 1

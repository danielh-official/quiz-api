import pytest
from conftest import answer, make_deck, make_questions, question_in
from pydantic import ValidationError

from app.models import Review
from app.schemas import OptionIn, QuestionIn, QuestionUpdate
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
def test_question_shape_is_enforced(type, count, correct, error):
    with pytest.raises(ValidationError, match=error):
        QuestionIn(type=type, stem="S", options=[OptionIn(text=f"o{i}", correct=i in correct) for i in range(count)])


def test_option_texts_must_be_distinct():
    options = [OptionIn(text="Same", correct=True)] + [OptionIn(text=t) for t in ("same ", "b", "c")]
    with pytest.raises(ValidationError, match="distinct"):
        QuestionIn(type="single", stem="S", options=options)


def test_option_ids_survive_reordering_and_edits(db, user):
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


def test_reviews_keep_meaning_after_reorder(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    session = study.start_session(db, user, deck.id)
    answer(db, user, session["session_id"], q)
    content.update_question(db, user, q.id, QuestionUpdate(options=[OptionIn(**o) for o in reversed(q.options)]))

    review = db.query(Review).one()
    picked = next(o for o in q.options if o["id"] == review.selected[0])
    assert picked["correct"]


def test_grading_is_exact_match(db, user):
    deck = make_deck(db, user)
    q = content.create_questions(db, user, deck.id, [question_in("S2", "select_two")])[0]
    session = study.start_session(db, user, deck.id)
    right = next(o["id"] for o in q.options if o["correct"])
    wrong = next(o["id"] for o in q.options if not o["correct"])
    one_right_one_wrong = [right, wrong]
    from app.schemas import AnswerIn

    result = study.submit_answer(
        db, user, session["session_id"], AnswerIn(question_id=q.id, selected=one_right_one_wrong, confidence="confident")
    )
    assert result["correct"] is False
    assert result["misconception"] is True


def test_partial_update_keeps_other_fields(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    updated = content.update_question(db, user, q.id, QuestionUpdate(stem="New stem"))
    assert updated.stem == "New stem"
    assert updated.explanation == "because"
    assert len(updated.options) == 4


def test_update_is_revalidated(db, user):
    deck = make_deck(db, user)
    [q] = make_questions(db, user, deck)
    with pytest.raises(ValidationError, match="exactly 5 options"):
        content.update_question(db, user, q.id, QuestionUpdate(type="select_two"))


def test_questions_move_only_to_own_decks(db, user, other):
    deck = make_deck(db, user)
    theirs = make_deck(db, other, "Theirs")
    [q] = make_questions(db, user, deck)
    with pytest.raises(NotFound):
        content.update_question(db, user, q.id, QuestionUpdate(deck_id=theirs.id))
    with pytest.raises(NotFound):
        content.get_question(db, other, q.id)


def test_search_matches_option_text_not_json_keys(db, user):
    deck = make_deck(db, user)
    make_questions(db, user, deck, 2)
    assert len(content.search_questions(db, user, "Q1 option")["questions"]) == 1
    assert content.search_questions(db, user, "correct")["questions"] == []


def correct_positions(options) -> set[int]:
    return {i for i, o in enumerate(options) if o["correct"]}


def test_stored_option_order_is_shuffled(db, user):
    deck = make_deck(db, user)
    questions = make_questions(db, user, deck, 40)  # authored with the answer always first
    assert len({min(correct_positions(q.options)) for q in questions}) > 1

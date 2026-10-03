from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    AnswerIn,
    CardUpdate,
    DeckCreate,
    DeckUpdate,
    ExamCreate,
    ExamUpdate,
    QuestionIn,
    QuestionUpdate,
    SessionCreate,
    SessionUpdate,
    SettingsUpdate,
)
from app.services import content, exams, stats, study

ArchiveFilter = Literal["active", "archived", "all"]

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
Me = Annotated[User, Depends(current_user)]
Json = dict[str, Any]


@router.get("/me")
def get_me(user: Me) -> Json:
    return content.user_dict(user)


@router.patch("/me")
def update_me(data: SettingsUpdate, db: Db, user: Me) -> Json:
    return content.update_settings(db, user, data)


@router.delete("/me", status_code=204)
def delete_me(db: Db, user: Me) -> Response:
    content.delete_user(db, user)
    return Response(status_code=204)


@router.get("/decks")
def list_decks(db: Db, user: Me, archived: bool = False) -> list[Json]:
    return content.list_decks(db, user, archived=archived)


@router.post("/decks", status_code=201)
def create_deck(data: DeckCreate, db: Db, user: Me) -> Json:
    return content.deck_dict(content.create_deck(db, user, data))


@router.get("/decks/{deck_id}")
def get_deck(
    deck_id: int,
    db: Db,
    user: Me,
    page: int = 1,
    suspended: bool = False,
    archived_children: bool = False,
) -> Json:
    return content.deck_detail(db, user, deck_id, page, suspended=suspended, archived_children=archived_children)


@router.patch("/decks/{deck_id}")
def update_deck(deck_id: int, data: DeckUpdate, db: Db, user: Me) -> Json:
    return content.deck_dict(content.update_deck(db, user, deck_id, data))


@router.delete("/decks/{deck_id}", status_code=204)
def delete_deck(deck_id: int, db: Db, user: Me) -> Response:
    content.delete_deck(db, user, deck_id)
    return Response(status_code=204)


@router.post("/decks/{deck_id}/questions", status_code=201)
def create_questions(
    deck_id: int, questions: Annotated[list[QuestionIn], Body(min_length=1, max_length=50)], db: Db, user: Me
) -> list[Json]:
    return [content.describe(q) for q in content.create_questions(db, user, deck_id, questions)]


@router.get("/questions/search")
def search_questions(
    db: Db,
    user: Me,
    q: Annotated[str, Query()],
    deck_id: int | None = None,
    archived: Annotated[ArchiveFilter, Query(description='"active", "archived", or "all".')] = "active",
) -> Json:
    return content.search_questions(db, user, q, deck_id, archived=archived)


@router.get("/questions/{question_id}")
def get_question(question_id: int, db: Db, user: Me) -> Json:
    return content.describe(content.get_question(db, user, question_id))


@router.patch("/questions/{question_id}")
def update_question(question_id: int, data: QuestionUpdate, db: Db, user: Me) -> Json:
    return content.describe(content.update_question(db, user, question_id, data))


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: int, db: Db, user: Me) -> Response:
    content.delete_question(db, user, question_id)
    return Response(status_code=204)


@router.put("/questions/{question_id}/card")
def update_card(question_id: int, data: CardUpdate, db: Db, user: Me) -> Json:
    return content.update_card(db, user, question_id, data)


@router.post("/decks/{deck_id}/sessions", status_code=201)
def start_session(deck_id: int, db: Db, user: Me, data: SessionCreate | None = None) -> Json:
    return study.start_session(db, user, deck_id, data.size if data else None)


@router.get("/sessions/{session_id}/next")
def next_question(session_id: int, db: Db, user: Me) -> Json:
    return study.next_question(db, user, session_id)


@router.post("/sessions/{session_id}/answers")
def submit_answer(session_id: int, answer: AnswerIn, db: Db, user: Me) -> Json:
    return study.submit_answer(db, user, session_id, answer)


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, data: SessionUpdate, db: Db, user: Me) -> Json:
    return study.update_session(db, user, session_id, data)


@router.get("/exams")
def list_exams(
    db: Db,
    user: Me,
    upcoming: Annotated[bool, Query(description="Only incomplete exams with starts_at null or in the future.")] = False,
) -> list[Json]:
    return [exams.exam_dict(e) for e in exams.list_exams(db, user, upcoming=upcoming)]


@router.post("/exams", status_code=201)
def create_exam(data: ExamCreate, db: Db, user: Me) -> Json:
    return exams.exam_dict(exams.create_exam(db, user, data))


@router.get("/exams/{exam_id}")
def get_exam(exam_id: int, db: Db, user: Me) -> Json:
    return exams.exam_dict(exams.get_exam(db, user, exam_id))


@router.patch("/exams/{exam_id}")
def update_exam(exam_id: int, data: ExamUpdate, db: Db, user: Me) -> Json:
    return exams.exam_dict(exams.update_exam(db, user, exam_id, data))


@router.delete("/exams/{exam_id}", status_code=204)
def delete_exam(exam_id: int, db: Db, user: Me) -> Response:
    exams.delete_exam(db, user, exam_id)
    return Response(status_code=204)


@router.get("/stats")
def get_stats(
    db: Db,
    user: Me,
    deck_id: int | None = None,
    exam_date: date | None = None,
    exam_id: int | None = None,
) -> Json:
    return stats.performance(db, user, deck_id, exam_date, exam_id)

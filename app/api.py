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
    SqlAttempt,
    SqlProblemCreate,
    SqlProblemUpdate,
    SqlTestCaseIn,
    SqlTestCaseUpdate,
    SqlTopicCreate,
)
from app.services import content, exams, sql, stats, study

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


@router.get("/sql/topics")
def list_sql_topics(db: Db, user: Me) -> list[Json]:
    return sql.list_topics(db, user)


@router.post("/sql/topics", status_code=201)
def create_sql_topic(data: SqlTopicCreate, db: Db, user: Me) -> Json:
    return sql.topic_dict(sql.create_topic(db, user, data))


@router.get("/sql/topics/{topic}")
def get_sql_topic(topic: str, db: Db, user: Me) -> Json:
    return sql.topic_detail(db, user, topic)


@router.delete("/sql/topics/{topic}", status_code=204)
def delete_sql_topic(topic: str, db: Db, user: Me) -> Response:
    sql.delete_topic(db, user, topic)
    return Response(status_code=204)


@router.post("/sql/topics/{topic}/problems", status_code=201)
def create_sql_problem(topic: str, data: SqlProblemCreate, db: Db, user: Me) -> Json:
    problem = sql.create_problem(db, user, topic, data)
    return sql.problem_detail(db, user, topic, problem.slug)


@router.get("/sql/topics/{topic}/problems/{problem}")
def get_sql_problem(topic: str, problem: str, db: Db, user: Me) -> Json:
    return sql.problem_detail(db, user, topic, problem)


@router.patch("/sql/topics/{topic}/problems/{problem}")
def update_sql_problem(topic: str, problem: str, data: SqlProblemUpdate, db: Db, user: Me) -> Json:
    sql.update_problem(db, user, topic, problem, data)
    return sql.problem_detail(db, user, topic, problem)


@router.delete("/sql/topics/{topic}/problems/{problem}", status_code=204)
def delete_sql_problem(topic: str, problem: str, db: Db, user: Me) -> Response:
    sql.delete_problem(db, user, topic, problem)
    return Response(status_code=204)


@router.post("/sql/topics/{topic}/problems/{problem}/test-cases", status_code=201)
def add_sql_test_case(topic: str, problem: str, data: SqlTestCaseIn, db: Db, user: Me) -> Json:
    case = sql.add_test_case(db, user, topic, problem, data)
    return sql.case_view(sql.get_problem(db, user, topic, problem)[1], case)


@router.patch("/sql/topics/{topic}/problems/{problem}/test-cases/{case_id}")
def update_sql_test_case(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    topic: str, problem: str, case_id: int, data: SqlTestCaseUpdate, db: Db, user: Me
) -> Json:
    case = sql.set_test_case_hidden(db, user, topic, problem, case_id, data.hidden)
    return sql.case_view(sql.get_problem(db, user, topic, problem)[1], case)


@router.delete("/sql/topics/{topic}/problems/{problem}/test-cases/{case_id}", status_code=204)
def delete_sql_test_case(topic: str, problem: str, case_id: int, db: Db, user: Me) -> Response:
    sql.delete_test_case(db, user, topic, problem, case_id)
    return Response(status_code=204)


@router.post("/sql/topics/{topic}/problems/{problem}/run")
def run_sql_tests(topic: str, problem: str, attempt: SqlAttempt, db: Db, user: Me) -> Json:
    return sql.run_tests(db, user, topic, problem, attempt)


@router.post("/sql/topics/{topic}/problems/{problem}/submissions", status_code=201)
def submit_sql(topic: str, problem: str, attempt: SqlAttempt, db: Db, user: Me) -> Json:
    submission = sql.submit(db, user, topic, problem, attempt)
    return sql.get_submission(db, user, topic, problem, submission.id)


@router.get("/sql/topics/{topic}/problems/{problem}/submissions/{submission_id}")
def get_sql_submission(topic: str, problem: str, submission_id: int, db: Db, user: Me) -> Json:
    return sql.get_submission(db, user, topic, problem, submission_id)

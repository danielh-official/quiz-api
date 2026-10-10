"""SQL practice: topics, problems, test cases, test runs and submissions. Separate from decks and FSRS.

Expected output is never stored: it comes from running the problem's reference query on each test case, so a test
case is only input rows. A problem is solved in a dialect when a submission in that dialect passes every case.
"""

import re
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SqlProblem, SqlSubmission, SqlTestCase, SqlTopic, User
from app.schemas import (
    SqlAttempt,
    SqlDialect,
    SqlProblemCreate,
    SqlProblemUpdate,
    SqlTestCaseIn,
    SqlTopicCreate,
)
from app.services import Invalid, NotFound
from app.services.sql_runner import DIALECT_LABELS, QueryResult, compare, load_error, run_query

EXAMPLE_ROWS = 5  # rows shown per table in a problem's example; the rest is "…"
STORED_ROWS = 50  # rows kept per result in a submission


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80].strip("-") or "untitled"


# Topics


def get_topic(db: Session, user: User, slug: str) -> SqlTopic:
    topic = db.scalar(select(SqlTopic).where(SqlTopic.user_id == user.id, SqlTopic.slug == slug))
    if topic is None:
        raise NotFound(f"Topic {slug!r} not found.")
    return topic


def topic_dict(topic: SqlTopic) -> dict[str, Any]:
    return {"slug": topic.slug, "name": topic.name, "description": topic.description}


def _solved(db: Session, problem_ids: list[int]) -> dict[int, list[str]]:
    """Dialects each problem has a fully passing submission in, in DIALECT_LABELS order."""
    if not problem_ids:
        return {}
    rows = db.execute(
        select(SqlSubmission.problem_id, SqlSubmission.dialect)
        .where(SqlSubmission.problem_id.in_(problem_ids), SqlSubmission.passed == SqlSubmission.total)
        .distinct()
    ).all()
    solved: dict[int, list[str]] = {pid: [] for pid in problem_ids}
    for problem_id, dialect in rows:
        solved[problem_id].append(dialect)
    order = list(DIALECT_LABELS)
    return {pid: sorted(ds, key=order.index) for pid, ds in solved.items()}


def list_topics(db: Session, user: User) -> list[dict[str, Any]]:
    topics = db.scalars(select(SqlTopic).where(SqlTopic.user_id == user.id).order_by(SqlTopic.name, SqlTopic.id)).all()
    problems = db.execute(select(SqlProblem.id, SqlProblem.topic_id).join(SqlTopic).where(SqlTopic.user_id == user.id)).all()
    solved = _solved(db, [pid for pid, _ in problems])
    counts: dict[int, list[int]] = {t.id: [0, 0] for t in topics}
    for problem_id, topic_id in problems:
        counts[topic_id][0] += 1
        counts[topic_id][1] += bool(solved[problem_id])
    return [{**topic_dict(t), "problems": counts[t.id][0], "solved": counts[t.id][1]} for t in topics]


def create_topic(db: Session, user: User, data: SqlTopicCreate) -> SqlTopic:
    slug = data.slug or slugify(data.name)
    if db.scalar(select(SqlTopic.id).where(SqlTopic.user_id == user.id, SqlTopic.slug == slug)) is not None:
        raise Invalid("slug", f"You already have a topic at {slug!r}.")
    topic = SqlTopic(user_id=user.id, slug=slug, name=data.name, description=data.description)
    db.add(topic)
    db.commit()
    return topic


def delete_topic(db: Session, user: User, slug: str) -> None:
    db.delete(get_topic(db, user, slug))
    db.commit()


def topic_detail(db: Session, user: User, slug: str) -> dict[str, Any]:
    topic = get_topic(db, user, slug)
    problems = db.scalars(
        select(SqlProblem).where(SqlProblem.topic_id == topic.id).order_by(SqlProblem.created_at, SqlProblem.id)
    ).all()
    solved = _solved(db, [p.id for p in problems])
    return {
        "topic": topic_dict(topic),
        "problems": [{"slug": p.slug, "title": p.title, "solved_dialects": solved[p.id]} for p in problems],
    }


# Problems


def get_problem(db: Session, user: User, topic_slug: str, problem_slug: str) -> tuple[SqlTopic, SqlProblem]:
    topic = get_topic(db, user, topic_slug)
    problem = db.scalar(select(SqlProblem).where(SqlProblem.topic_id == topic.id, SqlProblem.slug == problem_slug))
    if problem is None:
        raise NotFound(f"Problem {problem_slug!r} not found.")
    return topic, problem


def _cases(db: Session, problem: SqlProblem) -> list[SqlTestCase]:
    return list(db.scalars(select(SqlTestCase).where(SqlTestCase.problem_id == problem.id).order_by(SqlTestCase.id)).all())


def _expected(problem: SqlProblem, case: SqlTestCase | SqlTestCaseIn) -> QueryResult:
    return run_query(problem.tables, case.data, problem.reference_query, cast(SqlDialect, problem.reference_dialect))


def _check_case(problem: SqlProblem, case: SqlTestCaseIn, label: str) -> None:
    """The case's rows fit the tables and the reference query answers it."""
    error = load_error(problem.tables, case.data)
    if error:
        raise Invalid("test_cases", f"{label}: {error}")
    expected = _expected(problem, case)
    if expected.error:
        raise Invalid("reference_query", f"Fails on {label.lower()}: {expected.error}")


def create_problem(db: Session, user: User, topic_slug: str, data: SqlProblemCreate) -> SqlProblem:
    topic = get_topic(db, user, topic_slug)
    slug = data.slug or slugify(data.title)
    if db.scalar(select(SqlProblem.id).where(SqlProblem.topic_id == topic.id, SqlProblem.slug == slug)) is not None:
        raise Invalid("slug", f"This topic already has a problem at {slug!r}.")
    if all(case.hidden for case in data.test_cases):
        raise Invalid("test_cases", "At least one test case must be visible; the first visible one is the example.")
    problem = SqlProblem(
        topic_id=topic.id,
        slug=slug,
        title=data.title,
        description=data.description,
        tables=[t.model_dump() for t in data.tables],
        reference_query=data.reference_query,
        reference_dialect=data.reference_dialect,
        order_matters=data.order_matters,
    )
    for i, case in enumerate(data.test_cases, 1):
        _check_case(problem, case, f"Test case {i}")
    db.add(problem)
    db.flush()
    db.add_all(SqlTestCase(problem_id=problem.id, data=case.data, hidden=case.hidden) for case in data.test_cases)
    db.commit()
    return problem


def update_problem(db: Session, user: User, topic_slug: str, problem_slug: str, data: SqlProblemUpdate) -> SqlProblem:
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    changes = data.model_dump(exclude_unset=True)
    for name, value in changes.items():
        if value is None:
            raise Invalid(name, "Cannot be null.")
        setattr(problem, name, value)
    if {"reference_query", "reference_dialect"} & changes.keys():
        for i, case in enumerate(_cases(db, problem), 1):
            expected = _expected(problem, case)
            if expected.error:
                db.rollback()
                raise Invalid("reference_query", f"Fails on test case {i}: {expected.error}")
    db.commit()
    return problem


def delete_problem(db: Session, user: User, topic_slug: str, problem_slug: str) -> None:
    db.delete(get_problem(db, user, topic_slug, problem_slug)[1])
    db.commit()


def _shown(result: QueryResult, limit: int) -> dict[str, Any]:
    return {"columns": result.columns, "rows": result.rows[:limit], "more": max(len(result.rows) - limit, 0)}


def _table_view(problem: SqlProblem, case: SqlTestCase, limit: int) -> list[dict[str, Any]]:
    views = []
    for table in problem.tables:
        rows = case.data.get(table["name"]) or []
        columns = [c["name"] for c in table["columns"]]
        views.append({"name": table["name"], "columns": columns, "rows": rows[:limit], "more": max(len(rows) - limit, 0)})
    return views


def case_view(problem: SqlProblem, case: SqlTestCase, limit: int = EXAMPLE_ROWS) -> dict[str, Any]:
    """One test case as input tables plus the expected output, each cut to `limit` rows."""
    return {
        "id": case.id,
        "hidden": case.hidden,
        "input": _table_view(problem, case, limit),
        "expected": _shown(_expected(problem, case), limit),
    }


def problem_detail(db: Session, user: User, topic_slug: str, problem_slug: str) -> dict[str, Any]:
    topic, problem = get_problem(db, user, topic_slug, problem_slug)
    cases = _cases(db, problem)
    visible = [c for c in cases if not c.hidden]
    submissions = db.scalars(
        select(SqlSubmission).where(SqlSubmission.problem_id == problem.id).order_by(SqlSubmission.id.desc()).limit(20)
    ).all()
    return {
        "topic": topic_dict(topic),
        "slug": problem.slug,
        "title": problem.title,
        "description": problem.description,
        "tables": problem.tables,
        "order_matters": problem.order_matters,
        "reference_dialect": problem.reference_dialect,
        "example": case_view(problem, visible[0]) if visible else None,
        "test_cases": [case_view(problem, c, STORED_ROWS) for c in visible],
        "hidden_test_cases": len(cases) - len(visible),
        "solved_dialects": _solved(db, [problem.id])[problem.id],
        "submissions": [submission_summary(s) for s in submissions],
    }


# Test cases


def _get_case(db: Session, problem: SqlProblem, case_id: int) -> SqlTestCase:
    case = db.get(SqlTestCase, case_id)
    if case is None or case.problem_id != problem.id:
        raise NotFound(f"Test case {case_id} not found.")
    return case


def add_test_case(db: Session, user: User, topic_slug: str, problem_slug: str, data: SqlTestCaseIn) -> SqlTestCase:
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    _check_case(problem, data, "Test case")
    case = SqlTestCase(problem_id=problem.id, data=data.data, hidden=data.hidden)
    db.add(case)
    db.commit()
    return case


def set_test_case_hidden(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db: Session, user: User, topic_slug: str, problem_slug: str, case_id: int, hidden: bool
) -> SqlTestCase:
    """hidden=false is "add to my test cases": a hidden case a submit failed on starts running with Run tests."""
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    case = _get_case(db, problem, case_id)
    if hidden and not any(c.id != case.id and not c.hidden for c in _cases(db, problem)):
        raise Invalid("hidden", "Keep at least one visible test case; the first visible one is the example.")
    case.hidden = hidden
    db.commit()
    return case


def delete_test_case(db: Session, user: User, topic_slug: str, problem_slug: str, case_id: int) -> None:
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    case = _get_case(db, problem, case_id)
    cases = _cases(db, problem)
    if not any(c.id != case.id and not c.hidden for c in cases):
        raise Invalid("test_case", "Keep at least one visible test case; the first visible one is the example.")
    db.delete(case)
    db.commit()


# Running


def _judge(problem: SqlProblem, cases: list[SqlTestCase], attempt: SqlAttempt) -> list[dict[str, Any]]:
    """Per case: passed, a reason when not, and both results (cut to STORED_ROWS)."""
    results = []
    for case in cases:
        expected = _expected(problem, case)
        got = run_query(problem.tables, case.data, attempt.query, attempt.dialect)
        reason = compare(expected, got, problem.order_matters)
        results.append(
            {
                "case_id": case.id,
                "hidden": case.hidden,
                "passed": reason is None,
                "reason": reason,
                "expected": _shown(expected, STORED_ROWS),
                "got": {**_shown(got, STORED_ROWS), "error": got.error},
            }
        )
    return results


def run_tests(db: Session, user: User, topic_slug: str, problem_slug: str, attempt: SqlAttempt) -> dict[str, Any]:
    """Run against the visible test cases only. Nothing is saved."""
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    results = _judge(problem, [c for c in _cases(db, problem) if not c.hidden], attempt)
    return {
        "dialect": attempt.dialect,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "results": results,
    }


def submit(db: Session, user: User, topic_slug: str, problem_slug: str, attempt: SqlAttempt) -> SqlSubmission:
    """Run against every test case and save the attempt."""
    _, problem = get_problem(db, user, topic_slug, problem_slug)
    results = _judge(problem, _cases(db, problem), attempt)
    submission = SqlSubmission(
        problem_id=problem.id,
        dialect=attempt.dialect,
        query=attempt.query,
        passed=sum(r["passed"] for r in results),
        total=len(results),
        results=results,
    )
    db.add(submission)
    db.commit()
    return submission


def submission_summary(submission: SqlSubmission) -> dict[str, Any]:
    return {
        "id": submission.id,
        "dialect": submission.dialect,
        "passed": submission.passed,
        "total": submission.total,
        "solved": submission.passed == submission.total,
        "created_at": submission.created_at.isoformat(),
    }


def get_submission(db: Session, user: User, topic_slug: str, problem_slug: str, submission_id: int) -> dict[str, Any]:
    """The submission with each case's outcome. Hidden cases show their data only for the first one that failed,
    and only while it is still hidden; it can then be revealed with set_test_case_hidden(hidden=False)."""
    topic, problem = get_problem(db, user, topic_slug, problem_slug)
    submission = db.get(SqlSubmission, submission_id)
    if submission is None or submission.problem_id != problem.id:
        raise NotFound(f"Submission {submission_id} not found.")
    current = {c.id: c for c in _cases(db, problem)}
    first_hidden_failure = next((r["case_id"] for r in submission.results if r["hidden"] and not r["passed"]), None)
    results = []
    for number, result in enumerate(submission.results, 1):
        case = current.get(result["case_id"])
        shown = case is not None and (not case.hidden or case.id == first_hidden_failure)
        results.append(
            {
                "number": number,
                "case_id": result["case_id"],
                "passed": result["passed"],
                "reason": result["reason"],
                "hidden": case.hidden if case is not None else result["hidden"],
                "deleted": case is None,
                "input": _table_view(problem, case, STORED_ROWS) if shown and case is not None else None,
                "expected": result["expected"] if shown else None,
                "got": result["got"] if shown else None,
                "can_add": shown and case is not None and case.hidden,
            }
        )
    return {
        **submission_summary(submission),
        "topic": topic_dict(topic),
        "problem": {"slug": problem.slug, "title": problem.title},
        "query": submission.query,
        "results": results,
    }


def dialects() -> list[dict[str, str]]:
    return [{"id": key, "label": label} for key, label in DIALECT_LABELS.items()]


def dialect_label(dialect: SqlDialect | str) -> str:
    return DIALECT_LABELS.get(dialect, dialect)

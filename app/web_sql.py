"""SQL practice pages under /app/sql: topics, problems with an editor, test runs and submissions."""

from __future__ import annotations

import json
from typing import Any, cast, get_args

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.schemas import SqlAttempt, SqlDialect, SqlTopicCreate
from app.services import Invalid, sql
from app.web import TEMPLATES
from app.web_app import Db, WebUser, _form_error, is_htmx, render

router = APIRouter(prefix="/app/sql", include_in_schema=False)

TEMPLATES.filters.setdefault("dialect_label", sql.dialect_label)


def _crumbs(topic: dict[str, Any] | None = None, problem: dict[str, Any] | None = None) -> list[dict[str, str]]:
    crumbs = [{"name": "sql", "href": "/app/sql"}]
    if topic is not None:
        crumbs.append({"name": topic["name"], "href": f"/app/sql/{topic['slug']}"})
    if topic is not None and problem is not None:
        crumbs.append({"name": problem["title"], "href": f"/app/sql/{topic['slug']}/{problem['slug']}"})
    return crumbs


def _attempt(form: Any) -> SqlAttempt:
    dialect = str(form.get("dialect") or "sqlite")
    if dialect not in get_args(SqlDialect):
        dialect = "sqlite"
    return SqlAttempt(query=str(form.get("query") or ""), dialect=cast(SqlDialect, dialect))


@router.get("")
@router.get("/")
def topics(request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    return render("app/sql/topics.html", request, topics=sql.list_topics(db, user), error=None, topic_name="")


@router.post("")
@router.post("/")
async def create_topic(request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    name = str(form.get("name") or "").strip()
    try:
        topic = sql.create_topic(db, user, SqlTopicCreate(name=name))
    except (Invalid, ValidationError) as exc:
        topics_list = sql.list_topics(db, user)
        return render(
            "app/sql/topics.html", request, status_code=422, topics=topics_list, error=_form_error(exc), topic_name=name
        )
    return RedirectResponse(f"/app/sql/{topic.slug}", status_code=303)


@router.get("/{topic}")
def topic_page(topic: str, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    detail = sql.topic_detail(db, user, topic)
    return render("app/sql/topic.html", request, detail=detail, path=_crumbs())


@router.get("/{topic}/{problem}")
def problem_page(topic: str, problem: str, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    """The editor starts from the latest submission, so a reload or a revealed test case keeps the user's work."""
    request.state.user = user
    detail = sql.problem_detail(db, user, topic, problem)
    latest = sql.latest_submission(db, user, topic, problem)
    return render(
        "app/sql/problem.html",
        request,
        detail=detail,
        path=_crumbs(detail["topic"]),
        dialects=sql.dialects(),
        query=latest.query if latest else "",
        dialect=latest.dialect if latest else "sqlite",
        schema=json.dumps({t["name"]: [c["name"] for c in t["columns"]] for t in detail["tables"]}),
    )


@router.post("/{topic}/{problem}/run")
async def run_tests(topic: str, problem: str, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    """HTMX partial with the visible test cases' results. Without HTMX, the full problem page is not re-rendered:
    the Run button only exists as an HTMX control."""
    request.state.user = user
    form = await request.form()
    try:
        run = sql.run_tests(db, user, topic, problem, _attempt(form))
    except ValidationError:
        return render("app/sql/partials/run.html", request, status_code=422, run=None, error="Write a query first.")
    return render("app/sql/partials/run.html", request, run=run, error=None)


@router.post("/{topic}/{problem}/submit")
async def submit(topic: str, problem: str, request: Request, db: Db, user: WebUser) -> Response:
    request.state.user = user
    form = await request.form()
    try:
        submission = sql.submit(db, user, topic, problem, _attempt(form))
    except ValidationError:
        return RedirectResponse(f"/app/sql/{topic}/{problem}", status_code=303)
    target = f"/app/sql/{topic}/{problem}/submissions/{submission.id}"
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@router.get("/{topic}/{problem}/submissions/{submission_id}")
def submission_page(topic: str, problem: str, submission_id: int, request: Request, db: Db, user: WebUser) -> HTMLResponse:
    request.state.user = user
    submission = sql.get_submission(db, user, topic, problem, submission_id)
    return render(
        "app/sql/submission.html",
        request,
        submission=submission,
        path=_crumbs(submission["topic"], submission["problem"]),
    )


@router.post("/{topic}/{problem}/test-cases/{case_id}/show")
def show_test_case(topic: str, problem: str, case_id: int, db: Db, user: WebUser) -> RedirectResponse:
    """Add to my test cases: a hidden case a submit failed on becomes visible and runs with Run tests."""
    sql.set_test_case_hidden(db, user, topic, problem, case_id, hidden=False)
    return RedirectResponse(f"/app/sql/{topic}/{problem}#test-cases", status_code=303)

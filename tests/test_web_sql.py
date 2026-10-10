from collections.abc import Iterator

import pytest
from conftest import make_sql_problem
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import User
from app.services import sql
from app.web_app import web_claims

NAIVE = "SELECT salary FROM employees ORDER BY salary DESC LIMIT 1 OFFSET 1"
BASE = "/app/sql/aggregates/second-highest-salary"


@pytest.fixture
def client(user: User) -> Iterator[TestClient]:
    app.dependency_overrides[web_claims] = lambda: {"sub": user.subject, "login": user.login, "name": user.login, "email": None}
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_create_topic_and_list(client: TestClient) -> None:
    created = client.post("/app/sql", data={"name": "Window functions"})
    assert created.status_code == 303 and created.headers["location"] == "/app/sql/window-functions"
    assert "create-sql-problem" in client.get("/app/sql/window-functions").text
    page = client.get("/app/sql").text
    assert "Window functions" in page and 'href="/app/sql"' in page
    again = client.post("/app/sql", data={"name": "Window functions"})
    assert again.status_code == 422 and "already have a topic" in again.text


def test_problem_page_shows_example_and_editor(client: TestClient, db: Session, user: User) -> None:
    make_sql_problem(db, user)
    page = client.get(BASE)
    assert page.status_code == 200
    text = page.text
    assert "<strong>second highest</strong>" in text
    assert "data-sql-editor" in text and "/static/editor.js" in text
    assert "Submit also runs 1 hidden case." in text
    assert "MAX(salary)" not in text  # the reference query stays private
    assert 'option value="tsql"' in text and "SQL Server" in text


def test_run_returns_results_partial(client: TestClient, db: Session, user: User) -> None:
    make_sql_problem(db, user)
    ok = client.post(f"{BASE}/run", data={"query": NAIVE, "dialect": "mysql"}, headers={"HX-Request": "true"})
    assert "1 of 1 test case passed. Ready to submit." in ok.text and "<html" not in ok.text
    bad = client.post(f"{BASE}/run", data={"query": "SELEC 1", "dialect": "sqlite"})
    assert "0 of 1 test case passed" in bad.text and "syntax error" in bad.text
    empty = client.post(f"{BASE}/run", data={"query": "", "dialect": "sqlite"})
    assert empty.status_code == 422 and "Write a query first." in empty.text


def test_failed_submit_then_add_hidden_case(client: TestClient, db: Session, user: User) -> None:
    make_sql_problem(db, user)
    submitted = client.post(f"{BASE}/submit", data={"query": NAIVE, "dialect": "sqlite"})
    assert submitted.status_code == 303
    page = client.get(submitted.headers["location"]).text
    assert "Not yet · 1 of 2 passed" in page and "Add to my test cases" in page

    case_id = sql.get_submission(
        db, user, "aggregates", "second-highest-salary", int(submitted.headers["location"].rsplit("/", 1)[1])
    )["results"][1]["case_id"]
    shown = client.post(f"{BASE}/test-cases/{case_id}/show")
    assert shown.status_code == 303 and shown.headers["location"] == f"{BASE}#test-cases"
    problem = client.get(BASE).text
    assert "Run tests checks 2 cases." in problem
    assert NAIVE in problem  # the editor keeps the last submitted query

    good = "SELECT DISTINCT salary FROM employees ORDER BY salary DESC LIMIT 1 OFFSET 1"
    htmx = client.post(f"{BASE}/submit", data={"query": good, "dialect": "postgres"}, headers={"HX-Request": "true"})
    assert htmx.status_code == 204 and htmx.headers["HX-Redirect"].startswith(f"{BASE}/submissions/")
    assert "Solved · 2 of 2 passed" in client.get(htmx.headers["HX-Redirect"]).text
    assert "PostgreSQL" in client.get("/app/sql/aggregates").text


def test_other_users_problem_is_not_found(client: TestClient, db: Session, other: User) -> None:
    make_sql_problem(db, other)
    assert client.get(BASE).status_code == 404

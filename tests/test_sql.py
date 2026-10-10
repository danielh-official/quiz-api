from typing import Any

import pytest
from conftest import EMPLOYEES, make_sql_problem, make_user, sql_problem_in
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import SqlAttempt, SqlDialect, SqlProblemUpdate, SqlTestCaseIn, SqlTopicCreate
from app.services import Invalid, NotFound, sql
from app.services.sql_runner import compare, run_query

DATA = {"employees": [[1, "Ann", 100, "2020-01-05"], [2, "bob", 90.5, "2021-03-01"], [3, "Cy", 80, "2019-07-07"]]}
NAIVE = "SELECT salary FROM employees ORDER BY salary DESC LIMIT 1 OFFSET 1"
TOP_TWO = {
    "sqlite": "SELECT name FROM employees ORDER BY salary DESC LIMIT 2",
    "mysql": "SELECT `name` FROM employees ORDER BY salary DESC LIMIT 0, 2",
    "mariadb": "SELECT name FROM employees ORDER BY salary DESC LIMIT 2",
    "tsql": "SELECT TOP 2 name FROM employees ORDER BY salary DESC",
    "postgres": "SELECT name::text FROM employees ORDER BY salary DESC FETCH FIRST 2 ROWS ONLY",
}


def run(query: str, dialect: SqlDialect = "sqlite") -> Any:
    return run_query(EMPLOYEES, DATA, query, dialect)


@pytest.mark.parametrize("dialect", list(TOP_TWO))
def test_each_dialect_runs_its_own_syntax(dialect: SqlDialect) -> None:
    result = run(TOP_TWO[dialect], dialect)
    assert result.error is None
    assert result.rows == [["Ann"], ["bob"]]


def test_values_normalize_across_engines() -> None:
    sqlite = run("SELECT salary, hired FROM employees WHERE id = 2")
    duck = run("SELECT salary, hired FROM employees WHERE id = 2", "postgres")
    assert sqlite.rows == duck.rows == [[90.5, "2021-03-01"]]
    assert run("SELECT 2.0 AS x").rows == [[2]]


@pytest.mark.parametrize(
    ("query", "dialect", "message"),
    [
        ("ATTACH '/tmp/x.db' AS x", "sqlite", "not authorized"),
        ("PRAGMA table_info(employees)", "sqlite", "not authorized"),
        ("SELECT * FROM read_csv('/etc/passwd')", "postgres", "Permission Error"),
        ("COPY (SELECT 1) TO '/tmp/out.csv'", "postgres", "Permission Error"),
        ("SELECT 1; SELECT 2", "sqlite", "one statement"),
        ("SELECT 1; SELECT 2", "mysql", "one statement"),
        ("DELETE FROM employees", "sqlite", "no rows"),
    ],
)
def test_untrusted_sql_is_contained(query: str, dialect: SqlDialect, message: str) -> None:
    error = run(query, dialect).error
    assert error is not None and message in error


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_runaway_query_is_stopped(dialect: SqlDialect) -> None:
    query = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r"
    error = run(query, dialect).error
    assert error is not None and "stopped" in error


def test_compare_ignores_row_order_unless_asked() -> None:
    expected = run("SELECT name FROM employees ORDER BY id")
    reversed_rows = run("SELECT name FROM employees ORDER BY id DESC")
    assert compare(expected, reversed_rows, order_matters=False) is None
    assert compare(expected, reversed_rows, order_matters=True) is not None
    assert compare(expected, run("SELECT name, id FROM employees"), False) == "Expected 1 columns, got 2."
    assert compare(expected, run("SELECT name FROM employees WHERE id = 1"), False) == "Expected 3 rows, got 1."


def test_create_problem_checks_reference_and_data(db: Session, user: User) -> None:
    sql.create_topic(db, user, SqlTopicCreate(name="Aggregates"))
    with pytest.raises(Invalid, match="reference_query"):
        sql.create_problem(db, user, "aggregates", sql_problem_in(reference_query="SELECT nope FROM employees"))
    with pytest.raises(Invalid, match="needs 4 values"):
        sql.create_problem(db, user, "aggregates", sql_problem_in(test_cases=[{"data": {"employees": [[1]]}}]))
    with pytest.raises(Invalid, match="convert"):
        bad = [{"data": {"employees": [[1, "Ann", "lots", "2020-01-05"]]}}]
        sql.create_problem(db, user, "aggregates", sql_problem_in(test_cases=bad))
    with pytest.raises(Invalid, match="visible"):
        sql.create_problem(db, user, "aggregates", sql_problem_in(test_cases=[{"data": {}, "hidden": True}]))
    problem = sql.create_problem(db, user, "aggregates", sql_problem_in())
    assert problem.slug == "second-highest-salary"
    with pytest.raises(Invalid, match="already"):
        sql.create_problem(db, user, "aggregates", sql_problem_in())


def test_topics_are_private(db: Session, user: User, other: User) -> None:
    make_sql_problem(db, user)
    with pytest.raises(NotFound):
        sql.topic_detail(db, other, "aggregates")
    with pytest.raises(NotFound):
        sql.problem_detail(db, other, "aggregates", "second-highest-salary")
    sql.create_topic(db, other, SqlTopicCreate(name="Aggregates"))  # same slug, different user
    assert sql.list_topics(db, other)[0]["problems"] == 0


def test_problem_detail_shows_example_but_not_hidden_cases(db: Session, user: User) -> None:
    make_sql_problem(db, user)
    detail = sql.problem_detail(db, user, "aggregates", "second-highest-salary")
    assert detail["example"]["expected"] == {"columns": ["salary"], "rows": [[90]], "more": 0}
    assert detail["example"]["input"][0]["rows"][0] == [1, "Ann", 100, "2020-01-05"]
    assert len(detail["test_cases"]) == 1 and detail["hidden_test_cases"] == 1
    assert "reference_query" not in detail


def test_failed_submit_reveals_one_hidden_case_to_add(db: Session, user: User) -> None:
    make_sql_problem(db, user)
    ran = sql.run_tests(db, user, "aggregates", "second-highest-salary", SqlAttempt(query=NAIVE))
    assert (ran["passed"], ran["total"]) == (1, 1)

    submission = sql.submit(db, user, "aggregates", "second-highest-salary", SqlAttempt(query=NAIVE))
    assert (submission.passed, submission.total) == (1, 2)
    shown = sql.get_submission(db, user, "aggregates", "second-highest-salary", submission.id)
    failed = shown["results"][1]
    assert failed["passed"] is False and failed["can_add"]
    assert failed["expected"]["rows"] == [[80]] and failed["got"]["rows"] == [[100]]
    assert failed["input"][0]["rows"][1] == [2, "Bob", 100, "2021-03-01"]

    sql.set_test_case_hidden(db, user, "aggregates", "second-highest-salary", failed["case_id"], hidden=False)
    ran = sql.run_tests(db, user, "aggregates", "second-highest-salary", SqlAttempt(query=NAIVE))
    assert (ran["passed"], ran["total"]) == (1, 2)
    assert sql.get_submission(db, user, "aggregates", "second-highest-salary", submission.id)["results"][1]["can_add"] is False


def test_hidden_case_data_stays_hidden_when_passing(db: Session, user: User) -> None:
    problem = make_sql_problem(db, user)
    good = "SELECT MAX(salary) AS salary FROM employees WHERE salary < (SELECT MAX(salary) FROM employees)"
    submission = sql.submit(db, user, "aggregates", problem.slug, SqlAttempt(query=good, dialect="mysql"))
    shown = sql.get_submission(db, user, "aggregates", problem.slug, submission.id)
    assert shown["solved"] and shown["results"][1]["input"] is None and shown["results"][1]["expected"] is None


def test_solved_is_per_dialect(db: Session, user: User) -> None:
    problem = make_sql_problem(db, user)
    good = "SELECT MAX(salary) AS salary FROM employees WHERE salary < (SELECT MAX(salary) FROM employees)"
    sql.submit(db, user, "aggregates", problem.slug, SqlAttempt(query=good, dialect="tsql"))
    sql.submit(db, user, "aggregates", problem.slug, SqlAttempt(query=NAIVE, dialect="sqlite"))
    sql.submit(db, user, "aggregates", problem.slug, SqlAttempt(query=good, dialect="sqlite"))
    detail = sql.problem_detail(db, user, "aggregates", problem.slug)
    assert detail["solved_dialects"] == ["sqlite", "tsql"]
    assert len(detail["submissions"]) == 3
    assert sql.list_topics(db, user) == [
        {"slug": "aggregates", "name": "Aggregates", "description": None, "problems": 1, "solved": 1}
    ]


def test_update_rechecks_reference(db: Session, user: User) -> None:
    problem = make_sql_problem(db, user)
    with pytest.raises(Invalid, match="reference_query"):
        sql.update_problem(db, user, "aggregates", problem.slug, SqlProblemUpdate(reference_query="SELECT nope"))
    sql.update_problem(db, user, "aggregates", problem.slug, SqlProblemUpdate(title="2nd salary", order_matters=True))
    detail = sql.problem_detail(db, user, "aggregates", problem.slug)
    assert detail["title"] == "2nd salary" and detail["order_matters"] is True


def test_add_test_case_and_keep_one_visible(db: Session, user: User) -> None:
    problem = make_sql_problem(db, user)
    case = sql.add_test_case(db, user, "aggregates", problem.slug, SqlTestCaseIn(data={"employees": []}))
    detail = sql.problem_detail(db, user, "aggregates", problem.slug)
    assert detail["test_cases"][1]["expected"]["rows"] == [[None]]
    first = detail["test_cases"][0]["id"]
    sql.delete_test_case(db, user, "aggregates", problem.slug, case.id)
    with pytest.raises(Invalid, match="visible"):
        sql.set_test_case_hidden(db, user, "aggregates", problem.slug, first, hidden=True)
    with pytest.raises(Invalid, match="visible"):
        sql.delete_test_case(db, user, "aggregates", problem.slug, first)


def test_deleting_a_user_cascades(db: Session) -> None:
    owner = make_user(db, "carol", "3")
    make_sql_problem(db, owner)
    db.delete(owner)
    db.commit()

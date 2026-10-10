"""Run untrusted SQL against throwaway in-memory databases and compare results.

SQLite runs in the standard library's sqlite3. Every other dialect is translated by sqlglot and runs in DuckDB, so
syntax matches the dialect but some behavior follows DuckDB (integer division, collation). Each run gets a fresh
database with only the problem's tables: no files, no network, a time limit and a row cap.
ponytail: in-process isolation only; move this to its own Lambda (no VPC, no secrets) if it ever serves untrusted users.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import duckdb
import sqlglot
import sqlglot.errors

from app.schemas import SqlDialect

TIME_LIMIT = 2.0  # seconds per query
MAX_ROWS = 1000
MAX_ERROR = 500

# Dialect names as sqlglot knows them; MariaDB has no reader of its own, MySQL's is the closest.
SQLGLOT_DIALECTS: dict[str, str] = {"mysql": "mysql", "mariadb": "mysql", "tsql": "tsql", "postgres": "postgres"}
DIALECT_LABELS: dict[str, str] = {
    "sqlite": "SQLite",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "tsql": "SQL Server",
    "postgres": "PostgreSQL",
}
SQLITE_TYPES = {"integer": "INTEGER", "number": "REAL", "text": "TEXT", "date": "TEXT", "timestamp": "TEXT", "boolean": "INTEGER"}
DUCKDB_TYPES = {
    "integer": "BIGINT",
    "number": "DOUBLE",
    "text": "VARCHAR",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOLEAN",
}
SQLITE_DENIED = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "error": self.error, "truncated": self.truncated}


def normalize(value: Any) -> Any:  # pylint: disable=too-many-return-statements  # one return per type
    """JSON-safe, engine-neutral value: 2.0 == 2, floats to 4 places, dates as ISO text, booleans as 0/1."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return str(number)
        number = round(number, 4)
        return int(number) if number.is_integer() else number
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _quote(name: str) -> str:
    return f'"{name}"'


def _create_statements(tables: list[dict[str, Any]], types: dict[str, str]) -> list[str]:
    return [
        f"CREATE TABLE {_quote(t['name'])} ({', '.join(f'{_quote(c["name"])} {types[c["type"]]}' for c in t['columns'])})"
        for t in tables
    ]


def _inserts(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]]) -> list[tuple[str, list[list[Any]]]]:
    out = []
    for table in tables:
        rows = data.get(table["name"]) or []
        if rows:
            marks = ", ".join("?" for _ in table["columns"])
            out.append((f"INSERT INTO {_quote(table['name'])} VALUES ({marks})", rows))
    return out


def _fetch(cursor: Any) -> QueryResult:
    if cursor.description is None:
        return QueryResult(error="The statement returned no rows. Write a SELECT.")
    columns = [d[0] for d in cursor.description]
    raw = cursor.fetchmany(MAX_ROWS + 1)
    rows = [[normalize(v) for v in row] for row in raw[:MAX_ROWS]]
    return QueryResult(columns=columns, rows=rows, truncated=len(raw) > MAX_ROWS)


def _error(exc: BaseException) -> QueryResult:
    message = str(exc).strip() or type(exc).__name__
    return QueryResult(error=message[:MAX_ERROR])


def _run_sqlite(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]], query: str) -> QueryResult:
    conn = sqlite3.connect(":memory:")
    try:
        for statement in _create_statements(tables, SQLITE_TYPES):
            conn.execute(statement)
        for statement, rows in _inserts(tables, data):
            conn.executemany(statement, rows)
        conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in SQLITE_DENIED else sqlite3.SQLITE_OK)
        deadline = time.monotonic() + TIME_LIMIT
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            return _fetch(conn.execute(query))
        except sqlite3.OperationalError as exc:
            if str(exc) == "interrupted":
                return QueryResult(error=f"Query took longer than {TIME_LIMIT:g}s and was stopped.")
            return _error(exc)
        except (sqlite3.Error, ValueError) as exc:  # ProgrammingError: more than one statement
            return _error(exc)
    finally:
        conn.close()


def translate(query: str, dialect: SqlDialect) -> str:
    """The query in DuckDB's SQL. Raises ValueError for a parse error or anything but one statement."""
    try:
        statements = [s for s in sqlglot.transpile(query, read=SQLGLOT_DIALECTS[dialect], write="duckdb") if s.strip()]
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError(str(exc)) from exc
    if len(statements) != 1:
        raise ValueError("Write exactly one statement.")
    return statements[0]


def _run_duckdb(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]], query: str, dialect: SqlDialect) -> QueryResult:
    try:
        sql = translate(query, dialect)
    except ValueError as exc:
        return _error(exc)
    config: dict[str, str | bool | int | float | list[str]] = {
        "enable_external_access": False,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "threads": 1,
        "max_memory": "256MB",
        "lock_configuration": True,
    }
    conn = duckdb.connect(":memory:", config=config)
    try:
        for statement in _create_statements(tables, DUCKDB_TYPES):
            conn.execute(statement)
        for statement, rows in _inserts(tables, data):
            conn.executemany(statement, rows)
        timer = threading.Timer(TIME_LIMIT, conn.interrupt)
        timer.start()
        try:
            return _fetch(conn.execute(sql))
        except duckdb.InterruptException:
            return QueryResult(error=f"Query took longer than {TIME_LIMIT:g}s and was stopped.")
        except duckdb.Error as exc:
            return _error(exc)
        finally:
            timer.cancel()
    finally:
        conn.close()


def run_query(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]], query: str, dialect: SqlDialect) -> QueryResult:
    if dialect == "sqlite":
        return _run_sqlite(tables, data, query)
    return _run_duckdb(tables, data, query, dialect)


def load_error(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]]) -> str | None:
    """Why a test case's data doesn't fit the tables, or None. Checked once, when the case is saved."""
    names = {t["name"]: t for t in tables}
    for name, rows in data.items():
        if name not in names:
            return f"Unknown table {name!r}."
        width = len(names[name]["columns"])
        for row in rows:
            if len(row) != width:
                return f"Each {name} row needs {width} values."
    try:
        _check_load(tables, data)
    except duckdb.Error as exc:
        return str(exc)[:MAX_ERROR]
    return None


def _check_load(tables: list[dict[str, Any]], data: dict[str, list[list[Any]]]) -> None:
    """Load into DuckDB, whose strict types catch a 'hello' in an integer column or a bad date."""
    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for statement in _create_statements(tables, DUCKDB_TYPES):
            conn.execute(statement)
        for statement, rows in _inserts(tables, data):
            conn.executemany(statement, rows)
    finally:
        conn.close()


def _row_key(row: list[Any]) -> str:
    return json.dumps(row, default=str)


def compare(expected: QueryResult, got: QueryResult, order_matters: bool) -> str | None:
    """None when they match, else a short reason. Columns are compared by count, not name."""
    if got.error:
        return got.error
    if len(got.columns) != len(expected.columns):
        return f"Expected {len(expected.columns)} columns, got {len(got.columns)}."
    if len(got.rows) != len(expected.rows):
        return f"Expected {len(expected.rows)} rows, got {len(got.rows)}."
    if order_matters:
        return None if got.rows == expected.rows else "Rows differ (order matters for this problem)."
    same = sorted(map(_row_key, got.rows)) == sorted(map(_row_key, expected.rows))
    return None if same else "Rows differ."

"""
Database access.

Every request that changes data runs inside ONE short transaction obtained from
`transaction()`. The governance rules themselves live in PostgreSQL functions
(packages/db/migrations/003_governance.sql); the API only calls them, so the
rules hold even if someone bypasses the API.
"""
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False,
                      kwargs={"row_factory": dict_row})


@contextmanager
def transaction(actor_id: int | None = None) -> Iterator[psycopg.Connection]:
    """Commit on success, roll back on any exception."""
    with pool.connection() as conn:
        with conn.transaction():
            if actor_id is not None:
                # Used by the audit trigger to attribute edits to the user.
                conn.execute("SELECT set_config('app.actor_id', %s, true)", (str(actor_id),))
            yield conn


def fetch_all(conn, sql: str, params=()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def fetch_one(conn, sql: str, params=()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def scalar(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else next(iter(row.values()))

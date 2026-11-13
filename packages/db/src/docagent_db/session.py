"""Session scoping for RLS: every query on behalf of a visitor runs inside one of these."""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

import psycopg

DEMO_SESSION_ID = UUID("00000000-0000-0000-0000-000000000001")
"""Must match `demo_session_id()` in db/migrations/002_roles_and_rls.sql."""


@contextmanager
def session_scope(conn: psycopg.Connection, session_id: UUID) -> Generator[psycopg.Connection]:
    """Open a transaction with `app.session_id` set for its duration only (SET LOCAL)."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
        yield conn

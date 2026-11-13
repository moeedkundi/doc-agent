"""Row-level security: anonymous sessions must never see or touch each other's data.

Each test arranges rows for two sessions (A, B) plus the shared demo session as the owner,
then acts as `docagent_reader` / `docagent_app` via SET ROLE with `app.session_id` set,
exactly as the API and sqlstore server will.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from docagent_db import DEMO_SESSION_ID

# Tables the reader can query; each gets one row per session in the fixture.
DOC_TABLES = [
    "documents",
    "invoices",
    "invoice_lines",
    "purchase_orders",
    "po_lines",
    "invoice_po_matches",
]
# Tables the reader must not be able to query at all.
PRIVATE_TABLES = ["sessions", "runs", "trace_events", "review_actions", "llm_ledger"]


@dataclass(frozen=True)
class World:
    a: UUID
    b: UUID


def _seed_session(conn: psycopg.Connection, sid: UUID, tag: str) -> None:
    """One row in every session-scoped table, tagged so we can tell sessions apart."""
    conn.execute("INSERT INTO sessions (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (sid,))
    doc = conn.execute(
        "INSERT INTO documents (session_id, filename, doc_type, pages, storage_path) "
        "VALUES (%s, %s, 'invoice', 1, 'x') RETURNING id",
        (sid, f"{tag}.pdf"),
    ).fetchone()
    assert doc
    po = conn.execute(
        "INSERT INTO purchase_orders (session_id, document_id, po_number, total) "
        "VALUES (%s, %s, %s, 100) RETURNING id",
        (sid, doc[0], f"PO-{tag}"),
    ).fetchone()
    assert po
    conn.execute(
        "INSERT INTO po_lines (session_id, po_id, line_no, description) VALUES (%s, %s, 1, %s)",
        (sid, po[0], tag),
    )
    inv = conn.execute(
        "INSERT INTO invoices (session_id, document_id, invoice_number, po_number, total) "
        "VALUES (%s, %s, %s, %s, 100) RETURNING id",
        (sid, doc[0], f"INV-{tag}", f"PO-{tag}"),
    ).fetchone()
    assert inv
    conn.execute(
        "INSERT INTO invoice_lines (session_id, invoice_id, line_no, description) "
        "VALUES (%s, %s, 1, %s)",
        (sid, inv[0], tag),
    )
    conn.execute(
        "INSERT INTO invoice_po_matches (session_id, invoice_id, po_id, status) "
        "VALUES (%s, %s, %s, 'matched')",
        (sid, inv[0], po[0]),
    )
    run = conn.execute(
        "INSERT INTO runs (session_id, document_id) VALUES (%s, %s) RETURNING id",
        (sid, doc[0]),
    ).fetchone()
    assert run
    conn.execute(
        "INSERT INTO trace_events (session_id, run_id, seq, node, kind) "
        "VALUES (%s, %s, 1, 'extract', 'node_start')",
        (sid, run[0]),
    )
    conn.execute(
        "INSERT INTO review_actions (session_id, run_id, field_path, action) "
        "VALUES (%s, %s, 'total', 'approve')",
        (sid, run[0]),
    )
    conn.execute(
        "INSERT INTO llm_ledger (session_id, run_id, model, tokens_in, tokens_out, cost_usd) "
        "VALUES (%s, %s, 'm', 1, 1, 0.001)",
        (sid, run[0]),
    )


@pytest.fixture
def world(owner: psycopg.Connection) -> Iterator[World]:
    w = World(a=uuid4(), b=uuid4())
    _seed_session(owner, w.a, "A")
    _seed_session(owner, w.b, "B")
    _seed_session(owner, DEMO_SESSION_ID, "DEMO")
    yield w
    owner.execute("DELETE FROM sessions WHERE id = ANY(%s)", ([w.a, w.b],))
    owner.execute("DELETE FROM documents WHERE session_id = %s", (DEMO_SESSION_ID,))
    owner.execute("DELETE FROM llm_ledger WHERE session_id = %s", (DEMO_SESSION_ID,))


def _as(conn: psycopg.Connection, role: str, sid: UUID | None) -> None:
    """Switch the current transaction to `role`, scoped to session `sid`."""
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    if sid is not None:
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(sid),))


def _sessions_visible(conn: psycopg.Connection, table: str) -> set[UUID]:
    rows = conn.execute(
        sql.SQL("SELECT DISTINCT session_id FROM {}").format(sql.Identifier(table))
    ).fetchall()
    return {r[0] for r in rows}


@pytest.mark.parametrize("role", ["docagent_reader", "docagent_app"])
@pytest.mark.parametrize("table", DOC_TABLES)
def test_session_a_cannot_read_session_b(
    owner: psycopg.Connection, world: World, role: str, table: str
) -> None:
    with owner.transaction(force_rollback=True):
        _as(owner, role, world.a)
        assert _sessions_visible(owner, table) == {world.a, DEMO_SESSION_ID}
    with owner.transaction(force_rollback=True):
        _as(owner, role, world.b)
        assert _sessions_visible(owner, table) == {world.b, DEMO_SESSION_ID}


def test_session_b_rows_invisible_even_when_asked_for_by_id(
    owner: psycopg.Connection, world: World
) -> None:
    with owner.transaction(force_rollback=True):
        _as(owner, "docagent_reader", world.a)
        rows = owner.execute(
            "SELECT invoice_number FROM invoices WHERE session_id = %s OR po_number = 'PO-B'",
            (world.b,),
        ).fetchall()
        assert rows == []


@pytest.mark.parametrize("table", DOC_TABLES)
def test_no_session_set_sees_only_demo(owner: psycopg.Connection, world: World, table: str) -> None:
    with owner.transaction(force_rollback=True):
        _as(owner, "docagent_reader", None)
        assert _sessions_visible(owner, table) == {DEMO_SESSION_ID}


@pytest.mark.parametrize("table", PRIVATE_TABLES)
def test_reader_has_no_access_to_private_tables(
    owner: psycopg.Connection, world: World, table: str
) -> None:
    with (
        owner.transaction(force_rollback=True),
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        _as(owner, "docagent_reader", world.a)
        owner.execute(sql.SQL("SELECT 1 FROM {}").format(sql.Identifier(table)))


def test_reader_cannot_write(owner: psycopg.Connection, world: World) -> None:
    with (
        owner.transaction(force_rollback=True),
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        _as(owner, "docagent_reader", world.a)
        owner.execute("UPDATE invoices SET total = 0")


def test_reader_role_defaults_to_read_only_with_timeout(owner: psycopg.Connection) -> None:
    settings = owner.execute(
        "SELECT unnest(setconfig) FROM pg_db_role_setting s "
        "JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = 'docagent_reader'"
    ).fetchall()
    assert {"default_transaction_read_only=on", "statement_timeout=3s"} <= {r[0] for r in settings}


def test_app_cannot_insert_into_another_session(owner: psycopg.Connection, world: World) -> None:
    with (
        owner.transaction(force_rollback=True),
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
    ):
        _as(owner, "docagent_app", world.a)
        owner.execute(
            "INSERT INTO documents (session_id, filename, storage_path) VALUES (%s, 'x', 'x')",
            (world.b,),
        )


def test_app_cannot_modify_other_or_demo_sessions(owner: psycopg.Connection, world: World) -> None:
    with owner.transaction(force_rollback=True):
        _as(owner, "docagent_app", world.a)
        upd = owner.execute("UPDATE invoices SET total = 0 WHERE session_id <> %s", (world.a,))
        dele = owner.execute("DELETE FROM purchase_orders WHERE session_id <> %s", (world.a,))
        assert (upd.rowcount, dele.rowcount) == (0, 0)
        own = owner.execute("UPDATE invoices SET total = 1 WHERE session_id = %s", (world.a,))
        assert own.rowcount == 1


def test_app_sees_only_its_own_session_row(owner: psycopg.Connection, world: World) -> None:
    with owner.transaction(force_rollback=True):
        _as(owner, "docagent_app", world.a)
        ids = {r[0] for r in owner.execute("SELECT id FROM sessions").fetchall()}
        assert ids == {world.a}


def test_cleanup_deletes_expired_sessions_with_their_data(
    owner: psycopg.Connection, world: World
) -> None:
    owner.execute(
        "UPDATE sessions SET expires_at = now() - interval '1 hour' WHERE id = %s", (world.a,)
    )
    with owner.transaction():
        _as(owner, "docagent_app", None)
        deleted = {r[0] for r in owner.execute("SELECT * FROM cleanup_expired_sessions()")}
    assert deleted == {world.a}
    remaining = {r[0] for r in owner.execute("SELECT DISTINCT session_id FROM invoices")}
    assert world.a not in remaining
    assert {world.b, DEMO_SESSION_ID} <= remaining
    # llm_ledger keeps spend history (session_id nulled) so the daily budget stays correct.
    orphaned = owner.execute("SELECT count(*) FROM llm_ledger WHERE session_id IS NULL").fetchone()
    assert orphaned and orphaned[0] >= 1


def test_demo_session_never_expires(owner: psycopg.Connection) -> None:
    far_future = datetime.now(UTC) + timedelta(days=365 * 100)
    deleted = owner.execute("SELECT * FROM cleanup_expired_sessions(%s)", (far_future,)).fetchall()
    assert (DEMO_SESSION_ID,) not in deleted
    assert owner.execute("SELECT 1 FROM sessions WHERE id = %s", (DEMO_SESSION_ID,)).fetchone()

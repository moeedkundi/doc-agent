"""Database access for the API. Every call runs in a transaction with `app.session_id` set,
as `docagent_app`, so row-level security limits it to the caller's session."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

Connect = Callable[[], psycopg.Connection]
Row = dict[str, Any]


class Db:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @contextmanager
    def scoped(self, session_id: UUID) -> Generator[psycopg.Cursor[Row]]:
        with self._connect() as conn, conn.transaction():
            conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
            yield conn.cursor(row_factory=dict_row)

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")

    # -- sessions -------------------------------------------------------------------------
    def create_session(self, ip_hash: str, ttl_hours: int) -> UUID:
        sid = uuid4()
        with self.scoped(sid) as cur:
            cur.execute(
                "INSERT INTO sessions (id, ip_hash, expires_at) "
                "VALUES (%s, %s, now() + make_interval(hours => %s))",
                (sid, ip_hash, ttl_hours),
            )
        return sid

    def session_active(self, sid: UUID) -> bool:
        with self.scoped(sid) as cur:
            row = cur.execute(
                "SELECT 1 FROM sessions WHERE id = %s AND expires_at > now()", (sid,)
            ).fetchone()
        return row is not None

    # -- documents --------------------------------------------------------------------------
    def insert_document(
        self,
        sid: UUID,
        doc_id: UUID,
        filename: str,
        storage_path: str,
        pages: int,
        sample_id: str | None,
    ) -> None:
        with self.scoped(sid) as cur:
            cur.execute(
                "INSERT INTO documents (id, session_id, filename, storage_path, pages, sample_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (doc_id, sid, filename, storage_path, pages, sample_id),
            )

    def get_document(self, sid: UUID, doc_id: UUID) -> Row | None:
        with self.scoped(sid) as cur:
            return cur.execute(
                "SELECT id, filename, storage_path, pages, sample_id, doc_type FROM documents "
                "WHERE id = %s AND session_id = %s",
                (doc_id, sid),
            ).fetchone()

    # -- runs -------------------------------------------------------------------------------
    def create_run(
        self,
        sid: UUID,
        *,
        kind: str,
        document_id: UUID | None = None,
        question: str | None = None,
        replay: bool = False,
    ) -> UUID:
        run_id = uuid4()
        with self.scoped(sid) as cur:
            cur.execute(
                "INSERT INTO runs (id, session_id, kind, document_id, question, replay, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'queued')",
                (run_id, sid, kind, document_id, question, replay),
            )
        return run_id

    def get_run(self, sid: UUID, run_id: UUID) -> Row | None:
        with self.scoped(sid) as cur:
            return cur.execute(
                "SELECT id, kind, status, replay, document_id, question, started_at, "
                "finished_at, total_cost_usd, escalated, error_code, error, result_json "
                "FROM runs WHERE id = %s AND session_id = %s",
                (run_id, sid),
            ).fetchone()

    def events_after(self, sid: UUID, run_id: UUID, seq: int) -> list[Row]:
        with self.scoped(sid) as cur:
            return cur.execute(
                "SELECT seq, ts, node, kind, tool, model, tokens_in, tokens_out, latency_ms, "
                "cost_usd, output_json, error FROM trace_events "
                "WHERE run_id = %s AND seq > %s ORDER BY seq",
                (run_id, seq),
            ).fetchall()

    def spent_since(self, since: datetime) -> Decimal:
        # llm_ledger is not under RLS (the daily cap spans sessions); reading it needs no scope.
        with self._connect() as conn:
            row = conn.execute(
                "SELECT coalesce(sum(cost_usd), 0) FROM llm_ledger WHERE ts >= %s", (since,)
            ).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

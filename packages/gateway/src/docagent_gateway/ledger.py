"""Cost ledger: every provider call is recorded; budgets are checked against it.

`PostgresLedger` writes to the `llm_ledger` table (not under RLS, so daily totals span all
sessions). `MemoryLedger` is for tests and offline tools. Both are synchronous; the gateway
calls them from a worker thread.

Spend is split into pools: `demo` (visitors) and `eval` (evals/runner.py sessions). A
session's pool comes from `sessions.purpose`; each ledger row records the pool it was
charged to, so the demo's daily cap never counts eval spend.
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

import psycopg

Pool = Literal["demo", "eval"]


@dataclass(frozen=True)
class LedgerEntry:
    ts: datetime
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    run_id: UUID | None = None
    session_id: UUID | None = None
    pool: Pool = "demo"


class Ledger(Protocol):
    def record(self, entry: LedgerEntry) -> None: ...

    def spent_since(self, since: datetime, pool: Pool = "demo") -> Decimal: ...

    def spent_by_session(self, session_id: UUID) -> Decimal: ...

    def pool_of(self, session_id: UUID | None) -> Pool: ...


def start_of_utc_day(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


@dataclass
class MemoryLedger:
    entries: list[LedgerEntry] = field(default_factory=lambda: [])
    eval_sessions: set[UUID] = field(default_factory=lambda: set())
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, entry: LedgerEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def spent_since(self, since: datetime, pool: Pool = "demo") -> Decimal:
        with self._lock:
            return sum(
                (e.cost_usd for e in self.entries if e.ts >= since and e.pool == pool),
                Decimal(0),
            )

    def spent_by_session(self, session_id: UUID) -> Decimal:
        with self._lock:
            return sum((e.cost_usd for e in self.entries if e.session_id == session_id), Decimal(0))

    def pool_of(self, session_id: UUID | None) -> Pool:
        return "eval" if session_id in self.eval_sessions else "demo"


class PostgresLedger:
    """Ledger backed by `llm_ledger`. Opens a short connection per operation."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)

    def record(self, entry: LedgerEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO llm_ledger (ts, session_id, run_id, model, tokens_in, tokens_out, "
                "cost_usd, pool) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.ts,
                    entry.session_id,
                    entry.run_id,
                    entry.model,
                    entry.tokens_in,
                    entry.tokens_out,
                    entry.cost_usd,
                    entry.pool,
                ),
            )

    def spent_since(self, since: datetime, pool: Pool = "demo") -> Decimal:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT coalesce(sum(cost_usd), 0) FROM llm_ledger WHERE ts >= %s AND pool = %s",
                (since, pool),
            ).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def spent_by_session(self, session_id: UUID) -> Decimal:
        # Sessions live 24h, so "per session" is effectively per session-lifetime.
        with self._connect() as conn:
            row = conn.execute(
                "SELECT coalesce(sum(cost_usd), 0) FROM llm_ledger WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def pool_of(self, session_id: UUID | None) -> Pool:
        if session_id is None:
            return "demo"
        # sessions is under RLS for docagent_app: scope the read to that session.
        with self._connect() as conn, conn.transaction():
            conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
            row = conn.execute(
                "SELECT purpose FROM sessions WHERE id = %s", (session_id,)
            ).fetchone()
        return "eval" if row and row[0] == "eval" else "demo"


__all__ = [
    "Ledger",
    "LedgerEntry",
    "MemoryLedger",
    "Pool",
    "PostgresLedger",
    "start_of_utc_day",
]

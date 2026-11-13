"""Cost ledger: every provider call is recorded; budgets are checked against it.

`PostgresLedger` writes to the `llm_ledger` table (not under RLS, so daily totals span all
sessions). `MemoryLedger` is for tests and offline tools. Both are synchronous; the gateway
calls them from a worker thread.
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class LedgerEntry:
    ts: datetime
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    run_id: UUID | None = None
    session_id: UUID | None = None


class Ledger(Protocol):
    def record(self, entry: LedgerEntry) -> None: ...

    def spent_since(self, since: datetime) -> Decimal: ...

    def spent_by_session(self, session_id: UUID) -> Decimal: ...


def start_of_utc_day(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


@dataclass
class MemoryLedger:
    entries: list[LedgerEntry] = field(default_factory=lambda: [])
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, entry: LedgerEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def spent_since(self, since: datetime) -> Decimal:
        with self._lock:
            return sum((e.cost_usd for e in self.entries if e.ts >= since), Decimal(0))

    def spent_by_session(self, session_id: UUID) -> Decimal:
        with self._lock:
            return sum((e.cost_usd for e in self.entries if e.session_id == session_id), Decimal(0))


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
                "cost_usd) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.ts,
                    entry.session_id,
                    entry.run_id,
                    entry.model,
                    entry.tokens_in,
                    entry.tokens_out,
                    entry.cost_usd,
                ),
            )

    def spent_since(self, since: datetime) -> Decimal:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT coalesce(sum(cost_usd), 0) FROM llm_ledger WHERE ts >= %s", (since,)
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


__all__ = [
    "Ledger",
    "LedgerEntry",
    "MemoryLedger",
    "PostgresLedger",
    "start_of_utc_day",
]

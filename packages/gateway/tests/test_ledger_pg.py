"""PostgresLedger against a real (throwaway) database with the migrations applied."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg

from docagent_gateway import LedgerEntry, PostgresLedger


def test_records_and_sums_by_day_and_session(migrated_dsn: str, owner: psycopg.Connection) -> None:
    owner.execute("TRUNCATE llm_ledger")
    sid = uuid4()
    owner.execute("INSERT INTO sessions (id) VALUES (%s)", (sid,))
    ledger = PostgresLedger(migrated_dsn)
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    ledger.record(LedgerEntry(now, "gemini-3.8-flash", 1000, 200, Decimal("0.001500"), None, sid))
    ledger.record(LedgerEntry(now, "gemini-4-argon", 500, 100, Decimal("0.002000")))
    ledger.record(
        LedgerEntry(now - timedelta(days=1), "gemini-3.8-flash", 1, 1, Decimal("0.5"), None, sid)
    )

    midnight = datetime(2026, 11, 14, tzinfo=UTC)
    assert ledger.spent_since(midnight) == Decimal("0.003500")
    assert ledger.spent_by_session(sid) == Decimal("0.501500")
    rows = owner.execute(
        "SELECT model, tokens_in, tokens_out, cost_usd FROM llm_ledger WHERE ts >= %s ORDER BY id",
        (midnight,),
    ).fetchall()
    assert rows == [
        ("gemini-3.8-flash", 1000, 200, Decimal("0.001500")),
        ("gemini-4-argon", 500, 100, Decimal("0.002000")),
    ]


def test_pools_follow_the_session_purpose(migrated_dsn: str, owner: psycopg.Connection) -> None:
    owner.execute("TRUNCATE llm_ledger")
    visitor, evaluator = uuid4(), uuid4()
    owner.execute("INSERT INTO sessions (id) VALUES (%s)", (visitor,))
    owner.execute("INSERT INTO sessions (id, purpose) VALUES (%s, 'eval')", (evaluator,))
    ledger = PostgresLedger(migrated_dsn)
    assert (ledger.pool_of(visitor), ledger.pool_of(evaluator), ledger.pool_of(None)) == (
        "demo", "eval", "demo")  # fmt: skip
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    ledger.record(LedgerEntry(now, "m", 1, 1, Decimal("0.010"), None, visitor, "demo"))
    ledger.record(LedgerEntry(now, "m", 1, 1, Decimal("0.500"), None, evaluator, "eval"))
    midnight = datetime(2026, 11, 14, tzinfo=UTC)
    assert ledger.spent_since(midnight, "demo") == Decimal("0.010")
    assert ledger.spent_since(midnight, "eval") == Decimal("0.500")

"""A migrated throwaway database with two visitor sessions (A, B) and the demo session.

The store connects as `docagent_reader` / `docagent_app` via SET ROLE on an admin
connection, so RLS and grants apply exactly as they do with the real role logins.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from docagent_db import DEMO_SESSION_ID
from docagent_db.testing import empty_db_dsn, migrated_dsn, owner
from docagent_mcp_sqlstore.store import SqlSettings, SqlStore
from docagent_schemas import GroundTruth, InvoiceRecord, PurchaseOrderRecord

__all__ = ["empty_db_dsn", "migrated_dsn", "owner"]

SAMPLES = Path(__file__).resolve().parents[3] / "evals" / "dataset" / "samples"


def sample(doc_id: str) -> InvoiceRecord | PurchaseOrderRecord:
    return GroundTruth.model_validate_json((SAMPLES / f"{doc_id}.json").read_text()).record


def sample_invoice(doc_id: str) -> InvoiceRecord:
    record = sample(doc_id)
    assert isinstance(record, InvoiceRecord)
    return record


def as_role(dsn: str, role: str) -> Callable[[], psycopg.Connection]:
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(dsn, autocommit=True)
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        return conn

    return connect


@dataclass(frozen=True)
class World:
    store: SqlStore
    a: UUID
    b: UUID
    docs: dict[str, UUID]
    """sample doc_id -> documents.id"""
    records: dict[str, UUID]
    """sample doc_id -> invoices.id / purchase_orders.id"""


def _new_doc(owner: psycopg.Connection, session: UUID, name: str) -> UUID:
    row = owner.execute(
        "INSERT INTO documents (session_id, filename, storage_path) VALUES (%s, %s, %s) "
        "RETURNING id",
        (session, f"{name}.pdf", f"{name}.pdf"),
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.fixture(scope="module")
def world(migrated_dsn: str) -> Iterator[World]:
    store = SqlStore(
        reader=as_role(migrated_dsn, "docagent_reader"),
        app=as_role(migrated_dsn, "docagent_app"),
        settings=SqlSettings(max_rows=200, statement_timeout_ms=3000),
    )
    a, b = uuid4(), uuid4()
    docs: dict[str, UUID] = {}
    records: dict[str, UUID] = {}
    with psycopg.connect(migrated_dsn, autocommit=True) as owner:
        owner.execute("INSERT INTO sessions (id) VALUES (%s), (%s)", (a, b))
        # Session A: the price-drift pair. Session B: PO-1042 and its partial invoice.
        # Demo session: the fully-matched pair, visible to everyone.
        for session, ids in (
            (a, ("po_003", "inv_003")),
            (b, ("po_002", "inv_002")),
            (DEMO_SESSION_ID, ("po_001", "inv_001")),
        ):
            for doc_id in ids:
                docs[doc_id] = _new_doc(owner, session, doc_id)
    for session, ids in (
        (a, ("po_003", "inv_003")),
        (b, ("po_002", "inv_002")),
        (DEMO_SESSION_ID, ("po_001", "inv_001")),
    ):
        for doc_id in ids:
            records[doc_id] = store.save_record(session, docs[doc_id], sample(doc_id)).record_id
    yield World(store, a, b, docs, records)


@pytest.fixture
def fresh_session(migrated_dsn: str) -> UUID:
    sid = uuid4()
    with psycopg.connect(migrated_dsn, autocommit=True) as owner:
        owner.execute("INSERT INTO sessions (id) VALUES (%s)", (sid,))
    return sid


@pytest.fixture
def new_document(migrated_dsn: str) -> Callable[[UUID, str], UUID]:
    def make(session: UUID, name: str) -> UUID:
        with psycopg.connect(migrated_dsn, autocommit=True) as owner:
            return _new_doc(owner, session, name)

    return make

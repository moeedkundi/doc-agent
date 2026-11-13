"""sqlstore against a real migrated Postgres: session isolation, the execution layers
behind the gate, and the typed write/lookup tools."""

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from docagent_db import DEMO_SESSION_ID
from docagent_mcp_sqlstore.safety import SqlRejected
from docagent_mcp_sqlstore.store import SqlSettings, SqlStore, StoreError
from docagent_schemas import MatchIssue

from .conftest import World, sample, sample_invoice


def numbers(world: World, session: UUID, query: str) -> list[str]:
    return [str(r[0]) for r in world.store.run_select(query, session).rows]


# -- session isolation through run_select ---------------------------------------------


def test_session_a_cannot_see_session_b_rows(world: World) -> None:
    q = "SELECT invoice_number FROM invoices ORDER BY invoice_number"
    a_rows = numbers(world, world.a, q)
    b_rows = numbers(world, world.b, q)
    inv = {k: str(sample_invoice(k).invoice_number) for k in ("inv_001", "inv_002", "inv_003")}
    assert a_rows == sorted([inv["inv_003"], inv["inv_001"]])  # own + demo
    assert b_rows == sorted([inv["inv_002"], inv["inv_001"]])
    assert inv["inv_002"] not in a_rows and inv["inv_003"] not in b_rows


def test_asking_for_session_b_explicitly_returns_nothing(world: World) -> None:
    q = f"SELECT count(*) FROM invoices WHERE session_id = '{world.b}'"
    assert numbers(world, world.a, q) == ["0"]
    q = "SELECT count(*) FROM purchase_orders WHERE po_number = 'PO-1042'"  # B's PO
    assert numbers(world, world.a, q) == ["0"]
    assert numbers(world, world.b, q) == ["1"]


def test_isolation_holds_across_every_readable_table(world: World) -> None:
    for table in ("documents", "invoices", "invoice_lines", "purchase_orders", "po_lines"):
        q = f"SELECT count(DISTINCT session_id) FROM {table}"
        assert numbers(world, world.a, q) == ["2"], table  # A + demo, never B


def test_aggregates_only_cover_visible_rows(world: World) -> None:
    total = world.store.run_select("SELECT sum(total) FROM invoices", world.a).rows[0][0]
    expected = sample("inv_003").total + sample("inv_001").total  # type: ignore[operator]
    assert Decimal(str(total)) == expected


def test_a_session_with_nothing_saved_sees_only_the_demo(world: World, fresh_session: UUID) -> None:
    assert numbers(world, fresh_session, "SELECT count(*) FROM invoices") == ["1"]


# -- the gate is applied by run_select ---------------------------------------------------


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("DELETE FROM invoices", "not_select"),
        ("SELECT 1; DROP TABLE invoices", "multiple_statements"),
        ("SELECT * FROM sessions", "table_not_allowed"),
        ("SELECT pg_sleep(5)", "function_not_allowed"),
    ],
)
def test_run_select_applies_the_gate(world: World, query: str, code: str) -> None:
    with pytest.raises(SqlRejected) as exc:
        world.store.run_select(query, world.a)
    assert exc.value.code == code
    assert numbers(world, world.a, "SELECT count(*) FROM invoices") == ["2"]  # nothing changed


def test_transpiled_sql_runs_on_postgres(world: World) -> None:
    for q in [
        "SELECT date_trunc('month', invoice_date) m, count(*) FROM invoices GROUP BY 1",
        "SELECT extract(year FROM invoice_date), round(avg(total)::numeric, 2) FROM invoices "
        "GROUP BY 1",
        "SELECT to_char(invoice_date, 'YYYY-MM'), string_agg(invoice_number, ', ') "
        "FROM invoices GROUP BY 1",
        "SELECT po_number FROM purchase_orders UNION SELECT po_number FROM invoices",
        "WITH inv AS (SELECT po_number, sum(total) t FROM invoices GROUP BY 1) "
        "SELECT p.po_number, p.total - coalesce(inv.t, 0) AS open FROM purchase_orders p "
        "LEFT JOIN inv USING (po_number)",
        "SELECT rank() OVER (ORDER BY total DESC), invoice_number FROM invoices",
    ]:
        world.store.run_select(q, world.a)


def test_row_cap_and_truncated_flag(world: World) -> None:
    small = SqlStore(world.store._reader, world.store._app, SqlSettings(max_rows=2))  # pyright: ignore[reportPrivateUsage]
    out = small.run_select("SELECT line_no FROM invoice_lines ORDER BY 1", world.a)
    assert (out.row_count, out.truncated, out.sql.endswith("LIMIT 3")) == (2, True, True)


def test_values_are_json_friendly(world: World) -> None:
    out = world.store.run_select(
        "SELECT invoice_date, total, id FROM invoices WHERE po_number = 'PO-85044'", world.a
    )
    date_v, total_v, id_v = out.rows[0]
    assert date_v == "2026-08-29" and total_v == "5890.66" and isinstance(id_v, str)
    assert out.columns == ["invoice_date", "total", "id"]


# -- defence in depth: the layers behind the gate hold on their own ------------------------


def test_statement_timeout_cancels_slow_queries(world: World) -> None:
    quick = SqlStore(world.store._reader, world.store._app, SqlSettings(statement_timeout_ms=200))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(StoreError) as exc:
        quick.execute_checked("SELECT pg_sleep(2)", world.a)  # bypasses the gate on purpose
    assert exc.value.code == "query_timeout"


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO invoices (session_id, document_id) "
        "SELECT session_id, document_id FROM invoices",
        "UPDATE invoices SET total = 0",
        "DELETE FROM invoices",
    ],
)
def test_reader_transaction_cannot_write_even_without_the_gate(
    world: World, statement: str
) -> None:
    with pytest.raises(StoreError) as exc:
        world.store.execute_checked(statement, world.a)
    assert exc.value.code in {"read_only", "permission_denied"}
    assert numbers(world, world.a, "SELECT count(*) FROM invoices") == ["2"]


def test_reader_role_cannot_read_private_tables_even_without_the_gate(world: World) -> None:
    for table in ("sessions", "llm_ledger", "trace_events", "runs"):
        with pytest.raises(StoreError) as exc:
            world.store.execute_checked(f"SELECT * FROM {table}", world.a)
        assert exc.value.code == "permission_denied", table


def test_rls_holds_even_without_the_gate(world: World) -> None:
    out = world.store.execute_checked(
        f"SELECT count(*) FROM invoices WHERE session_id = '{world.b}'", world.a
    )
    assert out.rows == [[0]]


# -- schema ---------------------------------------------------------------------------------


def test_get_schema_lists_reader_tables_without_internal_columns(world: World) -> None:
    info = world.store.get_schema()
    tables = {t.name: t for t in info.tables}
    assert set(tables) == {
        "documents", "invoices", "invoice_lines", "purchase_orders", "po_lines",
        "invoice_po_matches",
    }  # fmt: skip
    doc_cols = {c.name for c in tables["documents"].columns}
    assert "storage_path" not in doc_cols and "session_id" not in doc_cols
    total = next(c for c in tables["invoices"].columns if c.name == "total")
    assert total.type == "numeric" and total.description


# -- typed writes and lookups -----------------------------------------------------------


def test_save_record_round_trips_through_get_purchase_order(world: World) -> None:
    found = world.store.get_purchase_order(world.a, "PO-85044")
    assert found.found and found.po_id == world.records["po_003"]
    assert found.purchase_order == sample("po_003")
    assert [p.invoice_id for p in found.prior_invoices] == [world.records["inv_003"]]
    assert found.prior_invoices[0].record == sample("inv_003")


def test_get_purchase_order_can_exclude_the_invoice_being_matched(world: World) -> None:
    found = world.store.get_purchase_order(
        world.a, "PO-85044", exclude_invoice_id=world.records["inv_003"]
    )
    assert found.prior_invoices == []


def test_get_purchase_order_is_session_scoped(world: World) -> None:
    assert not world.store.get_purchase_order(world.a, "PO-1042").found  # B's PO
    assert world.store.get_purchase_order(world.b, "PO-1042").found
    demo_po = sample("po_001").po_number  # type: ignore[union-attr]
    assert world.store.get_purchase_order(world.a, demo_po or "").found  # demo is shared


def test_cannot_save_onto_another_sessions_document(world: World) -> None:
    with pytest.raises(StoreError) as exc:
        world.store.save_record(world.a, world.docs["inv_002"], sample("inv_002"))
    assert exc.value.code == "document_not_found"


def test_cannot_save_onto_a_demo_document(world: World) -> None:
    with pytest.raises(StoreError):
        world.store.save_record(world.a, world.docs["inv_001"], sample("inv_001"))


def test_save_match_result(world: World) -> None:
    issue = MatchIssue(rule="unit_price_out_of_tolerance", message="+5%", invoice_line=1, po_line=1)
    saved = world.store.save_match_result(
        world.a, world.records["inv_003"], world.records["po_003"], "mismatch", [issue]
    )
    rows = world.store.run_select(
        "SELECT status, issues_json FROM invoice_po_matches", world.a
    ).rows
    assert rows[0][0] == "mismatch" and "unit_price_out_of_tolerance" in str(rows[0][1])
    assert isinstance(saved.match_id, UUID)
    assert numbers(world, world.b, "SELECT count(*) FROM invoice_po_matches") == ["0"]


def test_save_match_result_rejects_other_sessions_invoice(world: World) -> None:
    with pytest.raises(StoreError) as exc:
        world.store.save_match_result(world.a, world.records["inv_002"], None, "po_not_found", [])
    assert exc.value.code == "invoice_not_found"


def test_save_record_marks_document_type(
    world: World, fresh_session: UUID, new_document: Callable[[UUID, str], UUID]
) -> None:
    doc = new_document(fresh_session, "upload")
    world.store.save_record(fresh_session, doc, sample("po_003"))
    out = world.store.run_select(
        f"SELECT doc_type FROM documents WHERE id = '{doc}'", fresh_session
    )
    assert out.rows == [["purchase_order"]]
    _ = DEMO_SESSION_ID, uuid4


def test_connection_failures_do_not_leak_details(world: World) -> None:
    def refuse() -> psycopg.Connection:
        return psycopg.connect(
            "postgresql://docagent_reader:wrong@127.0.0.1:1/nope", connect_timeout=2
        )

    broken = SqlStore(reader=refuse, app=refuse)
    for call in (
        lambda: broken.run_select("SELECT 1", world.a),
        lambda: broken.get_purchase_order(world.a, "PO-1"),
    ):
        with pytest.raises(StoreError) as exc:
            call()
        assert exc.value.code == "database_unavailable"
        assert "127.0.0.1" not in str(exc.value) and "docagent_reader" not in str(exc.value)

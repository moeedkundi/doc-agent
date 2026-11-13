"""The SELECT-only gate on its own: what it rejects, what it lets through, how it limits."""

import pytest

from docagent_mcp_sqlstore.safety import SqlRejected, check_select


def rejected(sql: str) -> str:
    with pytest.raises(SqlRejected) as exc:
        check_select(sql, max_rows=200)
    return exc.value.code


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO invoices (invoice_number) VALUES ('x')",
        "insert into invoices select * from invoices",
        "UPDATE invoices SET total = 0",
        "UPDATE invoices SET total = 0 WHERE id IN (SELECT id FROM invoices)",
        "DELETE FROM invoices",
        "DELETE FROM invoices WHERE true RETURNING *",
        "DROP TABLE invoices",
        "DROP TABLE IF EXISTS invoices CASCADE",
        "TRUNCATE invoices",
        "ALTER TABLE invoices ADD COLUMN x int",
        "CREATE TABLE x AS SELECT * FROM invoices",
        "COPY invoices TO STDOUT",
        "SET app.session_id = '00000000-0000-0000-0000-000000000002'",
        "VACUUM invoices",
        "GRANT SELECT ON sessions TO docagent_reader",
    ],
)
def test_rejects_non_select_statements(sql: str) -> None:
    assert rejected(sql) == "not_select"


@pytest.mark.parametrize(
    "sql",
    [
        "WITH gone AS (DELETE FROM invoices RETURNING *) SELECT * FROM gone",
        "WITH x AS (UPDATE invoices SET total = 0 RETURNING id) SELECT count(*) FROM x",
        "WITH x AS (INSERT INTO invoices DEFAULT VALUES RETURNING id) SELECT * FROM x",
        "SELECT * INTO stolen FROM invoices",
        "SELECT * FROM invoices FOR UPDATE",
        "SELECT * FROM invoices FOR SHARE",
    ],
)
def test_rejects_writes_and_locks_hidden_inside_a_select(sql: str) -> None:
    assert rejected(sql) == "not_select"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DELETE FROM invoices",
        "SELECT 1; SELECT 2",
        "SELECT * FROM invoices; DROP TABLE invoices; --",
        "select 1;select 1",
    ],
)
def test_rejects_multiple_statements(sql: str) -> None:
    assert rejected(sql) == "multiple_statements"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sessions",
        "SELECT * FROM llm_ledger",
        "SELECT * FROM trace_events",
        "SELECT * FROM runs",
        "SELECT * FROM review_actions",
        "SELECT * FROM schema_migrations",
        "SELECT * FROM public.sessions",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM pg_user",
        "SELECT table_name FROM information_schema.tables",
        "SELECT i.* FROM invoices i JOIN sessions s ON s.id = i.session_id",
        "SELECT * FROM invoices WHERE session_id IN (SELECT id FROM sessions)",
        "SELECT * FROM generate_series(1, 100000000)",
        "SELECT * FROM dblink('host=evil', 'select 1') AS t(x int)",
    ],
)
def test_rejects_tables_not_on_allowlist(sql: str) -> None:
    assert rejected(sql) == "table_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT invoice_number FROM invoices WHERE pg_sleep(1) IS NULL",
        "SELECT * FROM invoices ORDER BY pg_sleep(1)",
        "WITH s AS (SELECT pg_sleep(5)) SELECT * FROM s",
        "SELECT PG_SLEEP(1)",
        "SELECT pg_sleep_for('5 minutes')",
        "SELECT current_setting('app.session_id')",
        "SELECT set_config('app.session_id', '00000000-0000-0000-0000-000000000002', false)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT generate_series(1, 100000000)",
        "SELECT txid_current()",
        "SELECT version()",
    ],
)
def test_rejects_functions_not_on_allowlist(sql: str) -> None:
    assert rejected(sql) == "function_not_allowed"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "empty_query"),
        ("-- just a comment", "empty_query"),
        ("SELEC 1", "sql_parse_error"),
        ("SELECT * FROM invoices LIMIT (SELECT 5)", "invalid_limit"),
    ],
)
def test_other_rejections(sql: str, code: str) -> None:
    assert rejected(sql) == code


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT seller_name, sum(total) AS t FROM invoices WHERE currency = 'USD' GROUP BY 1 "
        "ORDER BY 2 DESC",
        "SELECT i.invoice_number, m.status FROM invoices i JOIN invoice_po_matches m "
        "ON m.invoice_id = i.id WHERE m.status <> 'matched'",
        "WITH inv AS (SELECT po_number, sum(total) t FROM invoices GROUP BY 1) "
        "SELECT p.po_number, p.total - coalesce(inv.t, 0) AS open FROM purchase_orders p "
        "LEFT JOIN inv USING (po_number)",
        "SELECT date_trunc('month', invoice_date) m, count(*) FROM invoices GROUP BY 1",
        "SELECT extract(year FROM invoice_date), round(avg(total)::numeric, 2) FROM invoices "
        "GROUP BY 1",
        "SELECT po_number FROM purchase_orders UNION SELECT po_number FROM invoices",
        "SELECT CASE WHEN total > 1000 THEN 'big' ELSE 'small' END, lower(seller_name) "
        "FROM invoices WHERE seller_name ILIKE '%ltd%' AND invoice_date > DATE '2026-01-01'",
        "SELECT rank() OVER (ORDER BY total DESC), invoice_number FROM invoices",
        "SELECT to_char(invoice_date, 'YYYY-MM'), string_agg(invoice_number, ', ') "
        "FROM invoices GROUP BY 1",
        "WITH sessions AS (SELECT 1 AS n) SELECT n FROM sessions",  # a CTE, not the table
    ],
)
def test_accepts_realistic_analytical_queries(sql: str) -> None:
    check_select(sql, max_rows=200)


@pytest.mark.parametrize(
    ("sql", "limit"),
    [
        ("SELECT * FROM invoices", "LIMIT 201"),
        ("SELECT * FROM invoices LIMIT 10", "LIMIT 10"),
        ("SELECT * FROM invoices LIMIT 5000", "LIMIT 201"),
    ],
)
def test_limit_is_injected_or_clamped(sql: str, limit: str) -> None:
    assert check_select(sql, max_rows=200).sql.endswith(limit)


def test_set_operations_are_wrapped_so_the_cap_covers_everything() -> None:
    out = check_select("SELECT 1 AS n UNION ALL SELECT 2", max_rows=200).sql
    assert out.startswith("SELECT * FROM (") and out.endswith(") AS q LIMIT 201")

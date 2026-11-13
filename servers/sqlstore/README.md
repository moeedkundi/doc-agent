# docagent-sqlstore (MCP server)

Stores extracted invoices and purchase orders, and answers questions over them with SQL that
the model writes. Reads go through a **SELECT-only safety pipeline**. Writes happen only
through typed tools with parameterised statements: there is no tool that runs arbitrary SQL
writes.

## Run

```bash
uv run --env-file .env python -m servers.sqlstore        # or: python -m docagent_mcp_sqlstore
```

| Env var | Purpose |
|---|---|
| `MCP_SQLSTORE_URL` | e.g. `http://sqlstore:8103/mcp`. The server takes its **port and path** from this |
| `MCP_BIND_HOST` | bind address, default `127.0.0.1` |
| `MCP_SHARED_SECRET` | required, ≥ 16 chars. Every request must send it as the `x-mcp-secret` header, or gets `401` |
| `DATABASE_READER_URL` | `docagent_reader` login, used by `run_select` and `get_schema` |
| `DATABASE_APP_URL` | `docagent_app` login, used by the save/lookup tools |

It never uses the owner DSN: the reader and app roles are what make the read-only grants and
row-level security (RLS) apply. The row cap and statement timeout come from `config/limits.yaml`
(`sql.max_rows`, `sql.statement_timeout_ms`).

## Tools

| Tool | Input | Output |
|---|---|---|
| `get_schema` | — | `{tables[]: {name, description, columns[]: {name, type, description}}, notes[]}`. Only reader-visible tables; internal columns (`session_id`, `storage_path`) hidden |
| `run_select` | `sql`, `session_id` | `{sql (as executed), columns, rows (≤ 200), row_count, truncated}`. Numerics and dates come back as exact strings |
| `save_record` | `session_id`, `document_id`, `doc_type`, `record` (`InvoiceRecord`\|`PurchaseOrderRecord`) | `{record_id}`. Header plus lines in one transaction; also sets `documents.doc_type` |
| `get_purchase_order` | `session_id`, `po_number`, `exclude_invoice_id?` | `{found, po_id, purchase_order, prior_invoices[]: {invoice_id, record}}`. Prefers this session's PO, else the shared demo one; `prior_invoices` (oldest first) feeds `match_invoice_to_po` |
| `save_match_result` | `session_id`, `invoice_id`, `po_id` (null iff `po_not_found`), `status`, `issues[]` | `{match_id}` |

### `run_select` safety pipeline

1. **Parse** with sqlglot (Postgres dialect). There must be exactly one statement, so
   `SELECT 1; DROP …` gets `multiple_statements`.
2. **SELECT only:** the root must be SELECT/UNION/INTERSECT/EXCEPT. The whole tree is also
   checked, so INSERT/UPDATE/DELETE inside a CTE, `SELECT … INTO`, `FOR UPDATE`, COPY and SET
   are all rejected with `not_select`.
3. **Table allowlist:** `documents, invoices, invoice_lines, purchase_orders, po_lines,
   invoice_po_matches`. No other schema, no table functions (`table_not_allowed`).
4. **Function allowlist:** common aggregate, text, number, date and window functions only.
   `pg_sleep`, `set_config`, `current_setting`, `pg_read_file`, `generate_series` and the
   like are rejected (`function_not_allowed`).
5. **LIMIT** is injected, or clamped to 200, and the query fetches 201 rows to detect
   truncation. Set operations are wrapped so the cap covers the whole result.
6. **Execute** as `docagent_reader` in a `READ ONLY` transaction, with
   `SET LOCAL statement_timeout = 3000ms` and `app.session_id` set LOCAL. RLS limits rows to
   the caller's session plus the shared demo samples.

Each layer is tested on its own as well. Even when the gate is bypassed:
- the reader can't write (`read_only` / `permission_denied`);
- it can't read `sessions`, `llm_ledger`, `trace_events` or `runs`;
- RLS still hides other sessions;
- a slow query is cancelled (`query_timeout`).

Errors come back as `isError` with a `{code, message}` body:

| Group | Codes |
|---|---|
| Gate | `sql_parse_error`, `empty_query`, `multiple_statements`, `not_select`, `table_not_allowed`, `function_not_allowed`, `invalid_limit` |
| Execution | `query_timeout`, `query_error` (Postgres's message, so the model can fix its SQL), `permission_denied`, `read_only` |
| Writes | `document_not_found`, `invoice_not_found`, `wrong_doc_type`, `invalid_match` |

## Example

```bash
npx @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8103/mcp --transport http \
  --header "x-mcp-secret: $MCP_SHARED_SECRET" --method tools/call --tool-name run_select \
  --tool-arg "sql=SELECT seller_name, currency, sum(total) AS total FROM invoices GROUP BY 1, 2" \
  --tool-arg session_id=<visitor session uuid>
```

```json
{"sql": "SELECT seller_name, currency, SUM(total) AS total FROM invoices GROUP BY 1, 2 LIMIT 201",
 "columns": ["seller_name", "currency", "total"],
 "rows": [["Brightledger Stationers Ltd", "GBP", "5890.66"]], "row_count": 1, "truncated": false}
```

## Tests

`uv run pytest servers/sqlstore`:
- The gate on its own: 70 hostile and realistic queries.
- A migrated throwaway Postgres with two sessions and the demo session, covering isolation,
  the defence-in-depth layers, writes and lookups.
- The MCP surface, including auth.

The DB tests skip if Postgres isn't reachable. CI sets `REQUIRE_DB_TESTS=1` so they fail
instead.

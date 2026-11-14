"""Database access for the sqlstore server.

Two roles, two connection factories:
- reader (`docagent_reader`): run_select and get_schema. SELECT-only grants, RLS.
- app (`docagent_app`): save_record, get_purchase_order, save_match_result. Typed,
  parameterised statements only, RLS-scoped to the caller's session.

Every operation runs in its own transaction with `app.session_id` set LOCAL, so a pooled or
reused connection can never carry one visitor's session into another's query.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from docagent_mcp_sqlstore import catalog
from docagent_mcp_sqlstore.safety import ALLOWED_TABLES, check_select
from docagent_schemas import (
    Buyer,
    ColumnInfo,
    InvoiceLine,
    InvoiceRecord,
    JsonScalar,
    MatchIssue,
    MatchStatus,
    PriorInvoice,
    PurchaseOrderLine,
    PurchaseOrderLookup,
    PurchaseOrderRecord,
    SavedMatch,
    SavedRecord,
    SchemaInfo,
    SelectResult,
    Seller,
    TableInfo,
    Vendor,
)

Connect = Callable[[], psycopg.Connection]


class StoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class SqlSettings:
    max_rows: int = 200
    statement_timeout_ms: int = 3000


def _json_value(v: Any) -> JsonScalar:
    if v is None or isinstance(v, bool | int | float | str):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return json.dumps(v, default=str)


def _connect(factory: Connect) -> psycopg.Connection:
    try:
        return factory()
    except psycopg.OperationalError:
        # Don't hand connection details (host, port, role) back to the model.
        raise StoreError("database_unavailable", "the database is unavailable") from None


def _one[T](row: T | None) -> T:
    if row is None:  # INSERT ... RETURNING always yields a row
        raise StoreError("internal", "statement returned no row")
    return row


def _scope(conn: psycopg.Connection, session_id: UUID, timeout_ms: int | None = None) -> None:
    conn.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
    if timeout_ms is not None:
        conn.execute(
            sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(f"{timeout_ms}ms"))
        )


class SqlStore:
    def __init__(self, reader: Connect, app: Connect, settings: SqlSettings | None = None) -> None:
        self._reader = reader
        self._app = app
        self.settings = settings or SqlSettings()

    # -- read side (reader role) ------------------------------------------------------
    def get_schema(self) -> SchemaInfo:
        with self._connect_reader() as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            rows = conn.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(%s) "
                "ORDER BY table_name, ordinal_position",
                (sorted(ALLOWED_TABLES),),
            ).fetchall()
        columns: dict[str, list[ColumnInfo]] = {t: [] for t in sorted(ALLOWED_TABLES)}
        for table, column, data_type in rows:
            if column in catalog.HIDDEN_COLUMNS.get(table, set()):
                continue
            columns[table].append(
                ColumnInfo(
                    name=column,
                    type=data_type,
                    description=catalog.COLUMNS.get(table, {}).get(column),
                )
            )
        return SchemaInfo(
            tables=[
                TableInfo(name=t, description=catalog.TABLES[t], columns=cols)
                for t, cols in columns.items()
            ],
            notes=[
                "Write one PostgreSQL SELECT statement (CTEs and UNION are fine).",
                "Rows are automatically limited to your session's documents plus the shared "
                "demo samples; do not filter on session_id.",
                f"At most {self.settings.max_rows} rows are returned.",
                "No writes, no system catalogs, no functions outside common aggregate, "
                "text, number and date functions.",
            ],
        )

    def run_select(self, query: str, session_id: UUID) -> SelectResult:
        checked = check_select(query, max_rows=self.settings.max_rows)
        return self.execute_checked(checked.sql, session_id)

    def _connect_reader(self) -> psycopg.Connection:
        return _connect(self._reader)

    def _connect_app(self) -> psycopg.Connection:
        return _connect(self._app)

    def execute_checked(self, statement: str, session_id: UUID) -> SelectResult:
        """Run an already-validated SELECT as the reader: read-only, timed, session-scoped.

        Separate from run_select so tests can prove these layers hold on their own.
        """
        try:
            with self._connect_reader() as conn, conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                _scope(conn, session_id, self.settings.statement_timeout_ms)
                cur = conn.execute(statement.encode())
                names = [d.name for d in cur.description or []]
                fetched = cur.fetchall()
        except psycopg.errors.QueryCanceled:
            raise StoreError(
                "query_timeout",
                f"query exceeded {self.settings.statement_timeout_ms} ms and was cancelled",
            ) from None
        except psycopg.errors.InsufficientPrivilege as exc:
            raise StoreError("permission_denied", str(exc).splitlines()[0]) from None
        except psycopg.errors.ReadOnlySqlTransaction as exc:
            raise StoreError("read_only", str(exc).splitlines()[0]) from None
        except psycopg.Error as exc:
            # The model wrote this SQL; give it Postgres's message so it can fix it.
            raise StoreError("query_error", str(exc).splitlines()[0]) from None
        truncated = len(fetched) > self.settings.max_rows
        kept = fetched[: self.settings.max_rows]
        return SelectResult(
            sql=statement,
            columns=names,
            rows=[[_json_value(v) for v in row] for row in kept],
            row_count=len(kept),
            truncated=truncated,
        )

    # -- write side (app role) --------------------------------------------------------
    def _own_document(self, conn: psycopg.Connection, document_id: UUID, session_id: UUID) -> None:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = %s AND session_id = %s", (document_id, session_id)
        ).fetchone()
        if row is None:
            raise StoreError("document_not_found", f"document {document_id} not in this session")

    def save_record(
        self, session_id: UUID, document_id: UUID, record: InvoiceRecord | PurchaseOrderRecord
    ) -> SavedRecord:
        with self._connect_app() as conn, conn.transaction():
            _scope(conn, session_id)
            self._own_document(conn, document_id, session_id)
            if isinstance(record, InvoiceRecord):
                rid = self._insert_invoice(conn, session_id, document_id, record)
                doc_type = "invoice"
            else:
                rid = self._insert_po(conn, session_id, document_id, record)
                doc_type = "purchase_order"
            conn.execute(
                "UPDATE documents SET doc_type = %s WHERE id = %s", (doc_type, document_id)
            )
        return SavedRecord(record_id=rid)

    @staticmethod
    def _insert_invoice(conn: psycopg.Connection, sid: UUID, doc: UUID, r: InvoiceRecord) -> UUID:
        row = conn.execute(
            "INSERT INTO invoices (session_id, document_id, invoice_number, invoice_date, "
            "due_date, currency, po_number, seller_name, seller_address, seller_tax_id, "
            "buyer_name, buyer_address, subtotal, tax_amount, total) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                sid, doc, r.invoice_number, r.invoice_date, r.due_date, r.currency, r.po_number,
                r.seller.name, r.seller.address, r.seller.tax_id, r.buyer.name, r.buyer.address,
                r.subtotal, r.tax_amount, r.total,
            ),
        ).fetchone()  # fmt: skip
        row = _one(row)
        invoice_id: UUID = row[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO invoice_lines (session_id, invoice_id, line_no, sku, description, "
                "quantity, unit_price, line_total) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (sid, invoice_id, i + 1, li.sku, li.description, li.quantity, li.unit_price,
                     li.line_total)
                    for i, li in enumerate(r.line_items)
                ],
            )  # fmt: skip
        return invoice_id

    @staticmethod
    def _insert_po(conn: psycopg.Connection, sid: UUID, doc: UUID, r: PurchaseOrderRecord) -> UUID:
        row = conn.execute(
            "INSERT INTO purchase_orders (session_id, document_id, po_number, po_date, currency, "
            "payment_terms, buyer_name, buyer_address, vendor_name, vendor_address, "
            "vendor_tax_id, ship_to_address, requested_delivery_date, subtotal, tax_amount, "
            "total) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (
                sid, doc, r.po_number, r.po_date, r.currency, r.payment_terms, r.buyer.name,
                r.buyer.address, r.vendor.name, r.vendor.address, r.vendor.tax_id, r.ship_to,
                r.requested_delivery_date, r.subtotal, r.tax_amount, r.total,
            ),
        ).fetchone()  # fmt: skip
        row = _one(row)
        po_id: UUID = row[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO po_lines (session_id, po_id, line_no, sku, description, quantity, "
                "unit_price, line_total) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (sid, po_id, i + 1, li.sku, li.description, li.quantity, li.unit_price,
                     li.line_total)
                    for i, li in enumerate(r.line_items)
                ],
            )  # fmt: skip
        return po_id

    def get_purchase_order(
        self,
        session_id: UUID,
        po_number: str,
        exclude_invoice_id: UUID | None = None,
        exclude_invoice_number: str | None = None,
    ) -> PurchaseOrderLookup:
        with self._connect_app() as conn, conn.transaction():
            _scope(conn, session_id)
            cur = conn.cursor(row_factory=dict_row)
            po = cur.execute(
                "SELECT * FROM purchase_orders WHERE po_number = %s "
                "ORDER BY (session_id = %s) DESC, created_at DESC, id LIMIT 1",
                (po_number, session_id),
            ).fetchone()
            if po is None:
                return PurchaseOrderLookup(found=False)
            po_lines = cur.execute(
                "SELECT * FROM po_lines WHERE po_id = %s ORDER BY line_no", (po["id"],)
            ).fetchall()
            invoices = cur.execute(
                "SELECT * FROM invoices WHERE po_number = %s AND id IS DISTINCT FROM %s "
                "ORDER BY created_at, id",
                (po_number, exclude_invoice_id),
            ).fetchall()
            prior: list[PriorInvoice] = []
            duplicates: list[UUID] = []
            for inv in invoices:
                if exclude_invoice_number and inv["invoice_number"] == exclude_invoice_number:
                    duplicates.append(inv["id"])
                    continue
                lines = cur.execute(
                    "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY line_no",
                    (inv["id"],),
                ).fetchall()
                prior.append(PriorInvoice(invoice_id=inv["id"], record=_invoice_record(inv, lines)))
        return PurchaseOrderLookup(
            found=True,
            po_id=po["id"],
            purchase_order=_po_record(po, po_lines),
            prior_invoices=prior,
            duplicate_invoice_ids=duplicates,
        )

    def save_match_result(
        self,
        session_id: UUID,
        invoice_id: UUID,
        po_id: UUID | None,
        status: MatchStatus,
        issues: list[MatchIssue],
    ) -> SavedMatch:
        with self._connect_app() as conn, conn.transaction():
            _scope(conn, session_id)
            owned = conn.execute(
                "SELECT 1 FROM invoices WHERE id = %s AND session_id = %s", (invoice_id, session_id)
            ).fetchone()
            if owned is None:
                raise StoreError("invoice_not_found", f"invoice {invoice_id} not in this session")
            row = conn.execute(
                "INSERT INTO invoice_po_matches (session_id, invoice_id, po_id, status, "
                "issues_json) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (
                    session_id,
                    invoice_id,
                    po_id,
                    status,
                    json.dumps([i.model_dump(mode="json") for i in issues]),
                ),
            ).fetchone()
        row = _one(row)
        return SavedMatch(match_id=row[0])


def _invoice_record(h: dict[str, Any], lines: list[dict[str, Any]]) -> InvoiceRecord:
    return InvoiceRecord(
        invoice_number=h["invoice_number"],
        invoice_date=h["invoice_date"],
        due_date=h["due_date"],
        currency=h["currency"],
        po_number=h["po_number"],
        seller=Seller(name=h["seller_name"], address=h["seller_address"],
                      tax_id=h["seller_tax_id"]),
        buyer=Buyer(name=h["buyer_name"], address=h["buyer_address"]),
        line_items=[
            InvoiceLine(sku=li["sku"], description=li["description"], quantity=li["quantity"],
                        unit_price=li["unit_price"], line_total=li["line_total"])
            for li in lines
        ],
        subtotal=h["subtotal"],
        tax_amount=h["tax_amount"],
        total=h["total"],
    )  # fmt: skip


def _po_record(h: dict[str, Any], lines: list[dict[str, Any]]) -> PurchaseOrderRecord:
    return PurchaseOrderRecord(
        po_number=h["po_number"],
        po_date=h["po_date"],
        currency=h["currency"],
        payment_terms=h["payment_terms"],
        buyer=Buyer(name=h["buyer_name"], address=h["buyer_address"]),
        vendor=Vendor(name=h["vendor_name"], address=h["vendor_address"],
                      tax_id=h["vendor_tax_id"]),
        ship_to=h["ship_to_address"],
        requested_delivery_date=h["requested_delivery_date"],
        line_items=[
            PurchaseOrderLine(sku=li["sku"], description=li["description"],
                              quantity=li["quantity"], unit_price=li["unit_price"],
                              line_total=li["line_total"])
            for li in lines
        ],
        subtotal=h["subtotal"],
        tax_amount=h["tax_amount"],
        total=h["total"],
    )  # fmt: skip

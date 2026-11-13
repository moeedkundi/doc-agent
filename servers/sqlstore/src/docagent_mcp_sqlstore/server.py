"""The sqlstore MCP server: read-only SQL for Q&A, typed writes for the ingest graph.

There is deliberately no tool that executes arbitrary SQL writes (CLAUDE.md hard rule).
"""

import asyncio
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from docagent_mcp_common import tool, tool_error
from docagent_mcp_sqlstore.safety import SqlRejected
from docagent_mcp_sqlstore.store import SqlStore, StoreError
from docagent_schemas import (
    InvoiceRecord,
    MatchIssue,
    MatchStatus,
    PurchaseOrderLookup,
    PurchaseOrderRecord,
    RecordDocType,
    SavedMatch,
    SavedRecord,
    SchemaInfo,
    SelectResult,
)

INSTRUCTIONS = (
    "Saved invoices and purchase orders for the current visitor session. Use get_schema, then "
    "run_select with one PostgreSQL SELECT. Writes happen only through the typed save_* tools."
)
READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
SessionId = Annotated[UUID, Field(description="Visitor session; rows are scoped to it by RLS")]


async def _run[T](fn: Callable[[], T]) -> T:
    try:
        return await asyncio.to_thread(fn)
    except SqlRejected as exc:
        raise tool_error(exc.code, str(exc)) from None
    except StoreError as exc:
        raise tool_error(exc.code, str(exc)) from None


def create_server(store: SqlStore) -> MCPServer:
    server = MCPServer("docagent-sqlstore", instructions=INSTRUCTIONS, version="0.1.0")

    @tool(server, READ)
    async def get_schema() -> SchemaInfo:
        """Tables and columns you may query (descriptions included), plus the query rules."""
        return await _run(store.get_schema)

    @tool(server, READ)
    async def run_select(
        sql: Annotated[str, Field(min_length=1, max_length=8000)], session_id: SessionId
    ) -> SelectResult:
        """Run one read-only SELECT over the session's saved documents.

        The query is parsed and rejected unless it is a single SELECT over allowed tables
        and functions; a LIMIT is added or clamped; it runs as a read-only role with a
        statement timeout. Returns at most the row cap with `truncated` set if there were more.
        """
        return await _run(lambda: store.run_select(sql, session_id))

    @tool(server, WRITE)
    async def save_record(
        session_id: SessionId,
        document_id: UUID,
        doc_type: RecordDocType,
        record: InvoiceRecord | PurchaseOrderRecord,
    ) -> SavedRecord:
        """Save a validated (and, if needed, human-reviewed) record for a document."""
        if (doc_type == "invoice") != isinstance(record, InvoiceRecord):
            raise tool_error("wrong_doc_type", f"record does not match doc_type {doc_type!r}")
        return await _run(lambda: store.save_record(session_id, document_id, record))

    @tool(server, READ)
    async def get_purchase_order(
        session_id: SessionId,
        po_number: Annotated[str, Field(min_length=1, max_length=64)],
        exclude_invoice_id: Annotated[
            UUID | None, Field(description="The invoice being matched, if already saved")
        ] = None,
    ) -> PurchaseOrderLookup:
        """Look up a PO by number (this session first, then the shared demo samples), with the
        invoices already saved against it, oldest first. `found` is false if there is none."""
        return await _run(
            lambda: store.get_purchase_order(session_id, po_number, exclude_invoice_id)
        )

    @tool(server, WRITE)
    async def save_match_result(
        session_id: SessionId,
        invoice_id: UUID,
        po_id: UUID | None,
        status: MatchStatus,
        issues: list[MatchIssue],
    ) -> SavedMatch:
        """Record the outcome of matching an invoice to its PO (po_id null for po_not_found)."""
        if (status == "po_not_found") != (po_id is None):
            raise tool_error(
                "invalid_match", "po_id must be null exactly when status is po_not_found"
            )
        return await _run(
            lambda: store.save_match_result(session_id, invoice_id, po_id, status, issues)
        )

    return server

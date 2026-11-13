"""The validation MCP server: deterministic rules and PO matching. No LLM, no I/O."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from docagent_mcp_common import tool, tool_error
from docagent_mcp_validation import matching, rules
from docagent_mcp_validation.settings import ValidationSettings
from docagent_schemas import (
    Invoice,
    InvoiceRecord,
    MatchResult,
    MatchTolerances,
    PurchaseOrder,
    PurchaseOrderRecord,
    RecordDocType,
    RuleInfo,
    ValidationResult,
)

INSTRUCTIONS = (
    "Deterministic checks for extracted invoices and purchase orders. validate_record runs "
    "arithmetic, date, format and confidence rules; match_invoice_to_po compares an invoice "
    "with its purchase order. Pure functions: no side effects, no LLM."
)
PURE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def create_server(settings: ValidationSettings) -> MCPServer:
    server = MCPServer("docagent-validation", instructions=INSTRUCTIONS, version="0.1.0")

    @tool(server, PURE)
    def validate_record(
        doc_type: RecordDocType,
        record: Annotated[
            Invoice | PurchaseOrder | InvoiceRecord | PurchaseOrderRecord,
            Field(description="Extraction output (with per-field confidence) or a plain record"),
        ],
    ) -> ValidationResult:
        """Run every rule for `doc_type` and return issues with field paths and severities.

        `valid` is false when any issue has severity `error` or `review`. Low-confidence
        checks only apply when `record` is extraction output (fields wrapped with confidence).
        """
        try:
            return rules.validate(doc_type, record, settings)
        except TypeError as exc:
            raise tool_error("wrong_doc_type", str(exc)) from None

    @tool(server, PURE)
    def list_rules(doc_type: RecordDocType) -> list[RuleInfo]:
        """The rule catalogue for a document type (id, severity, description)."""
        return rules.list_rules(doc_type)

    @tool(server, PURE)
    def match_invoice_to_po(
        invoice: InvoiceRecord,
        purchase_order: PurchaseOrderRecord,
        prior_invoices: Annotated[
            list[InvoiceRecord] | None,
            Field(description="Invoices already saved against this PO, oldest first"),
        ] = None,
        tolerances: Annotated[
            MatchTolerances | None,
            Field(description="Override the configured tolerances"),
        ] = None,
    ) -> MatchResult:
        """Two-way match of an invoice against its purchase order.

        Status is `mismatch` if any issue is found, else `matched` when no PO quantity is
        left open, else `partial`. `line_matches` shows how invoice lines were paired with
        PO lines (by SKU, else fuzzy description).
        """
        return matching.match_invoice_to_po(
            invoice, purchase_order, prior_invoices, tolerances or settings.match
        )

    return server

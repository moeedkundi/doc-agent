"""Inputs/outputs of the extraction and validation MCP tools (SPEC §6.1, §6.2)."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from docagent_schemas.common import DocumentType, RecordDocType
from docagent_schemas.extracted import Invoice, PurchaseOrder
from docagent_schemas.fields import Field as ExtractedField
from docagent_schemas.records import InvoiceRecord, PurchaseOrderRecord

ModelTier = Literal["default", "strong"]
Severity = Literal["error", "review", "warning"]
"""error: a rule is violated. review: a human should look (e.g. low confidence).
warning: worth showing, never blocks."""


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LlmUsage(_Out):
    """What one tool call spent through the gateway (the agent logs it as a trace event)."""

    tier: ModelTier
    model: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int


# -- extraction ---------------------------------------------------------------------


class ClassificationResult(_Out):
    doc_type: DocumentType
    confidence: float = Field(ge=0, le=1)
    reason: str
    usage: LlmUsage


class ExtractionResult(_Out):
    doc_type: RecordDocType
    record: Invoice | PurchaseOrder
    pages: list[int]
    """1-based pages that were sent to the model."""
    usage: LlmUsage


FieldValue = date | float | str
"""Every extracted leaf is one of these (see extracted.py)."""


class ReextractResult(_Out):
    doc_type: RecordDocType
    fields: dict[str, ExtractedField[FieldValue]]
    """Only the requested field paths, e.g. {"line_items[1].unit_price": Field(...)}."""
    usage: LlmUsage


# -- validation ---------------------------------------------------------------------


class ValidationIssue(_Out):
    field_path: str
    """Path in the record, same syntax reextract_fields accepts ("" for record-level)."""
    severity: Severity
    rule: str
    message: str


class ValidationResult(_Out):
    valid: bool
    """True when there is no `error` and no `review` issue."""
    issues: list[ValidationIssue]


class RuleInfo(_Out):
    rule: str
    doc_types: list[RecordDocType]
    severity: Severity
    description: str


class MatchTolerances(_Out):
    unit_price_pct: Decimal = Field(default=Decimal("2.0"), ge=0)
    amount: Decimal = Field(default=Decimal("0.01"), ge=0)
    description_min_score: float = Field(default=85, ge=0, le=100)
    description_fallback_score: float = Field(default=60, ge=0, le=100)


# -- sqlstore -----------------------------------------------------------------------

JsonScalar = str | int | float | bool | None
"""SQL values as returned by run_select: numerics and dates are strings (exact, ISO 8601)."""


class ColumnInfo(_Out):
    name: str
    type: str
    description: str | None = None


class TableInfo(_Out):
    name: str
    description: str
    columns: list[ColumnInfo]


class SchemaInfo(_Out):
    tables: list[TableInfo]
    notes: list[str]
    """Rules the SQL must follow (SELECT only, allowed functions, row cap)."""


class SelectResult(_Out):
    sql: str
    """The statement actually executed (after LIMIT injection)."""
    columns: list[str]
    rows: list[list[JsonScalar]]
    row_count: int
    truncated: bool


class SavedRecord(_Out):
    record_id: UUID


class PriorInvoice(_Out):
    invoice_id: UUID
    record: InvoiceRecord


class PurchaseOrderLookup(_Out):
    found: bool
    po_id: UUID | None = None
    purchase_order: PurchaseOrderRecord | None = None
    prior_invoices: list[PriorInvoice] = []
    """Invoices already saved against this PO number, oldest first (input for matching)."""
    duplicate_invoice_ids: list[UUID] = []
    """Earlier saves of the invoice being matched (same invoice number): a re-submission,
    left out of `prior_invoices` so it is not billed against the PO twice."""


class SavedMatch(_Out):
    match_id: UUID


# -- reference ----------------------------------------------------------------------

STATIC_SOURCE = "DocAgent static demo table (illustrative values, not market or legal data)"


class FxConversion(_Out):
    amount: Decimal
    from_currency: str
    to_currency: str
    rate: Decimal
    """Units of to_currency per 1 unit of from_currency."""
    rate_date: date
    source: str


class VendorRecord(_Out):
    vendor_id: str
    legal_name: str
    tax_id: str
    country: str
    default_currency: str
    payment_terms: str


class VendorLookup(_Out):
    found: bool
    vendor: VendorRecord | None = None
    matched_on: Literal["tax_id", "name"] | None = None
    name_score: float | None = None
    tax_id_matches: bool | None = None
    """When both name and tax_id were given: does the tax ID agree with the master record?"""


class TaxRate(_Out):
    country: str
    category: Literal["standard", "reduced"]
    rate: Decimal
    effective_date: date
    source: str

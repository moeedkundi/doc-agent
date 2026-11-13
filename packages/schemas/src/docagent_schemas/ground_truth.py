"""Ground-truth sidecar written next to every synthetic document (evals/generator)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from docagent_schemas.common import RecordDocType
from docagent_schemas.matching import MatchIssueRule, MatchStatus
from docagent_schemas.records import InvoiceRecord, PurchaseOrderRecord

InjectedKind = Literal[
    # PO <-> invoice mismatches (caught by match_invoice_to_po)
    "price_drift",
    "over_billed_qty",
    "wrong_vendor",
    "missing_po",
    # single-document defects (caught by validate_record)
    "arithmetic_total",
    "arithmetic_line",
]


class Injection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: InjectedKind
    detail: str
    line: int | None = None
    """0-based line index affected, if any."""


class ExpectedMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MatchStatus
    rules: list[MatchIssueRule]
    """Issue rules the matcher must raise (order-insensitive)."""


class GroundTruth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    doc_type: RecordDocType
    file: str
    split: Literal["sample", "eval"]
    template: str
    scanned: bool
    pages: int
    record: InvoiceRecord | PurchaseOrderRecord
    references_po: str | None = None
    """doc_id of the PO this invoice was generated against (None if it has no real PO)."""
    injected: list[Injection] = []
    expected_match: ExpectedMatch | None = None
    """Expected match outcome when ingested in manifest order; None for POs and for
    invoices that carry no PO number."""

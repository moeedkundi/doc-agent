"""PO <-> invoice two-way match result types (SPEC §6.2)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

MatchStatus = Literal["matched", "partial", "mismatch", "po_not_found"]
"""
- matched: every check passes and the invoice uses up the PO's remaining quantity.
- partial: every check passes, but quantity remains open on the PO afterwards.
- mismatch: at least one check fails.
- po_not_found: the invoice cites a PO number that is not on file.
"""

MatchIssueRule = Literal[
    "vendor_mismatch",
    "currency_mismatch",
    "line_not_on_po",
    "qty_exceeds_remaining",
    "unit_price_out_of_tolerance",
    "total_exceeds_remaining",
    "po_not_found",
]


class MatchIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: MatchIssueRule
    message: str
    invoice_line: int | None = None
    """0-based index into the invoice's line_items, when the issue is line-specific."""
    po_line: int | None = None


class MatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MatchStatus
    issues: list[MatchIssue]

"""Pydantic schemas shared across DocAgent."""

from docagent_schemas.common import DocumentType, RecordDocType
from docagent_schemas.extracted import (
    ExtractedBuyer,
    ExtractedInvoiceLine,
    ExtractedPurchaseOrderLine,
    ExtractedSeller,
    ExtractedVendor,
    Invoice,
    PurchaseOrder,
)
from docagent_schemas.fields import Field
from docagent_schemas.ground_truth import ExpectedMatch, GroundTruth, InjectedKind, Injection
from docagent_schemas.matching import MatchIssue, MatchIssueRule, MatchResult, MatchStatus
from docagent_schemas.records import (
    Buyer,
    InvoiceLine,
    InvoiceRecord,
    PurchaseOrderLine,
    PurchaseOrderRecord,
    Seller,
    Vendor,
)

__version__ = "0.1.0"

__all__ = [
    "Buyer",
    "DocumentType",
    "ExpectedMatch",
    "ExtractedBuyer",
    "ExtractedInvoiceLine",
    "ExtractedPurchaseOrderLine",
    "ExtractedSeller",
    "ExtractedVendor",
    "Field",
    "GroundTruth",
    "InjectedKind",
    "Injection",
    "Invoice",
    "InvoiceLine",
    "InvoiceRecord",
    "MatchIssue",
    "MatchIssueRule",
    "MatchResult",
    "MatchStatus",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseOrderRecord",
    "RecordDocType",
    "Seller",
    "Vendor",
]

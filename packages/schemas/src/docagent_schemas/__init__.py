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
from docagent_schemas.matching import (
    LineMatch,
    MatchIssue,
    MatchIssueRule,
    MatchResult,
    MatchStatus,
)
from docagent_schemas.records import (
    Buyer,
    InvoiceLine,
    InvoiceRecord,
    PurchaseOrderLine,
    PurchaseOrderRecord,
    Seller,
    Vendor,
)
from docagent_schemas.tools import (
    ClassificationResult,
    ExtractionResult,
    FieldValue,
    LlmUsage,
    MatchTolerances,
    ModelTier,
    ReextractResult,
    RuleInfo,
    Severity,
    ValidationIssue,
    ValidationResult,
)

__version__ = "0.1.0"

__all__ = [
    "Buyer",
    "ClassificationResult",
    "DocumentType",
    "ExpectedMatch",
    "ExtractedBuyer",
    "ExtractedInvoiceLine",
    "ExtractedPurchaseOrderLine",
    "ExtractedSeller",
    "ExtractedVendor",
    "ExtractionResult",
    "Field",
    "FieldValue",
    "GroundTruth",
    "InjectedKind",
    "Injection",
    "Invoice",
    "InvoiceLine",
    "InvoiceRecord",
    "LineMatch",
    "LlmUsage",
    "MatchIssue",
    "MatchIssueRule",
    "MatchResult",
    "MatchStatus",
    "MatchTolerances",
    "ModelTier",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseOrderRecord",
    "RecordDocType",
    "ReextractResult",
    "RuleInfo",
    "Seller",
    "Severity",
    "ValidationIssue",
    "ValidationResult",
    "Vendor",
]

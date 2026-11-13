"""Inputs/outputs of the extraction and validation MCP tools (SPEC §6.1, §6.2)."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from docagent_schemas.common import DocumentType, RecordDocType
from docagent_schemas.extracted import Invoice, PurchaseOrder
from docagent_schemas.fields import Field as ExtractedField

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

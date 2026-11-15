"""Deterministic validation rules for invoices and purchase orders (SPEC §6.2). No LLM.

Each rule has an id, the document types it applies to, a severity and a check function.
Field paths in issues use the extraction syntax (`line_items[1].line_total`) so the agent
can hand failing paths straight to `reextract_fields`.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from docagent_mcp_validation.iso4217 import ISO_4217
from docagent_mcp_validation.settings import ValidationSettings
from docagent_schemas import (
    Invoice,
    InvoiceRecord,
    PurchaseOrder,
    PurchaseOrderRecord,
    RecordDocType,
    RuleInfo,
    Severity,
    ValidationIssue,
    ValidationResult,
)

AnyRecord = InvoiceRecord | PurchaseOrderRecord
AnyInput = Invoice | PurchaseOrder | InvoiceRecord | PurchaseOrderRecord


@dataclass(frozen=True)
class Subject:
    """What the rules look at: the plain record plus per-field confidence, if extracted."""

    doc_type: RecordDocType
    record: AnyRecord
    confidence: dict[str, float]


Check = Callable[[Subject, ValidationSettings], Iterator[tuple[str, str]]]
"""Yields (field_path, message) for each violation."""


@dataclass(frozen=True)
class Rule:
    id: str
    doc_types: tuple[RecordDocType, ...]
    severity: Severity
    description: str
    check: Check

    def info(self) -> RuleInfo:
        return RuleInfo(
            rule=self.id,
            doc_types=list(self.doc_types),
            severity=self.severity,
            description=self.description,
        )


BOTH: tuple[RecordDocType, ...] = ("invoice", "purchase_order")
INVOICE: tuple[RecordDocType, ...] = ("invoice",)
PO: tuple[RecordDocType, ...] = ("purchase_order",)

_REQUIRED: dict[RecordDocType, tuple[str, ...]] = {
    "invoice": ("invoice_number", "invoice_date", "currency", "seller.name", "total"),
    "purchase_order": ("po_number", "po_date", "currency", "vendor.name", "total"),
}

_TERMS = (
    re.compile(r"^net\s*(\d{1,3})(\s*days?)?$"),
    re.compile(r"^(\d{1,3})\s*days?(\s*net)?$"),
    re.compile(r"^\d+(\.\d+)?%?\s*/\s*\d{1,3}\s*,?\s*net\s*\d{1,3}$"),  # 2/10 Net 30
    re.compile(r"^(due on receipt|due upon receipt|cod|cash on delivery|prepaid|immediate)$"),
)


def _get(record: AnyRecord, path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        value = getattr(value, part, None)
    return value


def _close(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    return abs(a - b) <= tol


# -- checks ---------------------------------------------------------------------------


def _required(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    for path in _REQUIRED[s.doc_type]:
        value = _get(s.record, path)
        if value is None or (isinstance(value, str) and not value.strip()):
            yield path, f"{path} is missing"


def _has_lines(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    if not s.record.line_items:
        yield "line_items", "document has no line items"


def _currency(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    cur = s.record.currency
    if cur is not None and cur not in ISO_4217:
        yield "currency", f"{cur!r} is not an ISO 4217 currency code"


def _line_amount(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    for i, li in enumerate(s.record.line_items):
        if li.quantity is None or li.unit_price is None or li.line_total is None:
            continue
        expected = li.quantity * li.unit_price
        if not _close(expected, li.line_total, cfg.amount_tolerance):
            yield (
                f"line_items[{i}].line_total",
                f"line {i + 1}: {li.quantity} x {li.unit_price} = {expected:.2f}, "
                f"but line total is {li.line_total}",
            )


def _lines_sum(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    totals = [li.line_total for li in s.record.line_items]
    if s.record.subtotal is None or not totals or any(t is None for t in totals):
        return
    total = sum((t for t in totals if t is not None), Decimal(0))
    if not _close(total, s.record.subtotal, cfg.amount_tolerance):
        yield "subtotal", f"line totals sum to {total}, but subtotal is {s.record.subtotal}"


def _subtotal_tax(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if r.subtotal is None or r.tax_amount is None or r.total is None:
        return
    if not _close(r.subtotal + r.tax_amount, r.total, cfg.amount_tolerance):
        yield (
            "total",
            f"subtotal {r.subtotal} + tax {r.tax_amount} = {r.subtotal + r.tax_amount}, "
            f"but total is {r.total}",
        )


def _due_after_issue(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if (
        isinstance(r, InvoiceRecord)
        and r.invoice_date
        and r.due_date
        and r.due_date < r.invoice_date
    ):
        yield "due_date", f"due date {r.due_date} is before invoice date {r.invoice_date}"


def _delivery_after_order(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if (
        isinstance(r, PurchaseOrderRecord)
        and r.po_date
        and r.requested_delivery_date
        and r.requested_delivery_date < r.po_date
    ):
        yield (
            "requested_delivery_date",
            f"delivery date {r.requested_delivery_date} is before PO date {r.po_date}",
        )


def _po_number_format(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if (
        isinstance(r, PurchaseOrderRecord)
        and r.po_number
        and not cfg.po_number_re.match(r.po_number)
    ):
        yield "po_number", f"{r.po_number!r} does not look like a PO number"


def _invoice_po_ref_format(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if isinstance(r, InvoiceRecord) and r.po_number and not cfg.po_number_re.match(r.po_number):
        yield "po_number", f"PO reference {r.po_number!r} does not look like a PO number"


def _terms(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    r = s.record
    if isinstance(r, PurchaseOrderRecord) and r.payment_terms:
        text = " ".join(r.payment_terms.lower().split())
        if not any(p.match(text) for p in _TERMS):
            yield "payment_terms", f"payment terms {r.payment_terms!r} are not recognised"


def _duplicate_skus(s: Subject, _: ValidationSettings) -> Iterator[tuple[str, str]]:
    seen: dict[str, int] = {}
    for i, li in enumerate(s.record.line_items):
        if not li.sku:
            continue
        if li.sku in seen:
            yield f"line_items[{i}].sku", f"SKU {li.sku} repeats line {seen[li.sku] + 1}"
        else:
            seen[li.sku] = i


def _low_confidence(s: Subject, cfg: ValidationSettings) -> Iterator[tuple[str, str]]:
    for path, conf in s.confidence.items():
        if conf < cfg.review_confidence_threshold:
            yield (
                path,
                f"extraction confidence {conf:.2f} is below {cfg.review_confidence_threshold}",
            )


RULES: tuple[Rule, ...] = (
    Rule(
        "required_field",
        BOTH,
        "error",
        "Key fields (number, date, currency, party name, total) are present.",
        _required,
    ),
    Rule(
        "line_items_present", BOTH, "error", "The document has at least one line item.", _has_lines
    ),
    Rule("currency_iso4217", BOTH, "error", "Currency is a valid ISO 4217 code.", _currency),
    Rule(
        "line_amount",
        BOTH,
        "error",
        "quantity x unit price = line total (+/- tolerance).",
        _line_amount,
    ),
    Rule("lines_sum_to_subtotal", BOTH, "error", "Line totals add up to the subtotal.", _lines_sum),
    Rule("subtotal_plus_tax_equals_total", BOTH, "error", "subtotal + tax = total.", _subtotal_tax),
    Rule(
        "due_date_not_before_invoice_date",
        INVOICE,
        "error",
        "The due date is on or after the invoice date.",
        _due_after_issue,
    ),
    Rule(
        "invoice_po_reference_format",
        INVOICE,
        "warning",
        "A PO reference, if present, looks like a PO number.",
        _invoice_po_ref_format,
    ),
    Rule(
        "delivery_date_not_before_po_date",
        PO,
        "error",
        "The requested delivery date is on or after the PO date.",
        _delivery_after_order,
    ),
    Rule(
        "po_number_format",
        PO,
        "warning",
        "The PO number looks like a document number (validation.po_number_pattern).",
        _po_number_format,
    ),
    Rule(
        "payment_terms_parseable",
        PO,
        "review",
        "Payment terms are recognisable (Net N, N days, 2/10 Net 30, due on receipt).",
        _terms,
    ),
    Rule(
        "no_duplicate_skus", PO, "error", "No SKU appears on more than one line.", _duplicate_skus
    ),
    Rule(
        "low_confidence",
        BOTH,
        "review",
        "Every extracted field's confidence is at or above the review threshold.",
        _low_confidence,
    ),
)


def list_rules(doc_type: RecordDocType) -> list[RuleInfo]:
    return [r.info() for r in RULES if doc_type in r.doc_types]


def _confidences(extracted: Invoice | PurchaseOrder) -> dict[str, float]:
    out: dict[str, float] = {}

    def walk(obj: Any, prefix: str) -> None:
        if isinstance(obj, dict):
            d: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType]
            if "confidence" in d and "value" in d:
                out[prefix] = float(d["confidence"])
                return
            for k, v in d.items():
                walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                walk(v, f"{prefix}[{i}]")

    walk(extracted.model_dump(mode="json"), "")
    return out


def to_subject(doc_type: RecordDocType, record: AnyInput) -> Subject:
    is_invoice = isinstance(record, InvoiceRecord | Invoice)
    if is_invoice != (doc_type == "invoice"):
        raise TypeError(f"record is a {type(record).__name__}, not a {doc_type}")
    if isinstance(record, InvoiceRecord | PurchaseOrderRecord):
        return Subject(doc_type, record, {})
    return Subject(doc_type, record.to_record(), _confidences(record))


def validate(
    doc_type: RecordDocType, record: AnyInput, cfg: ValidationSettings
) -> ValidationResult:
    subject = to_subject(doc_type, record)
    issues = [
        ValidationIssue(field_path=path, severity=rule.severity, rule=rule.id, message=msg)
        for rule in RULES
        if doc_type in rule.doc_types
        for path, msg in rule.check(subject, cfg)
    ]
    valid = not any(i.severity in ("error", "review") for i in issues)
    return ValidationResult(valid=valid, issues=issues)

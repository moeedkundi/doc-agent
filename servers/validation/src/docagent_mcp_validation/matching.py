"""Two-way PO <-> invoice match (SPEC §6.2). Deterministic, no LLM.

Checks, in order:
1. vendor: tax IDs agree when both are present, otherwise names agree (fuzzy, legal-form
   suffixes ignored) -> `vendor_mismatch`
2. currency agrees -> `currency_mismatch`
3. each invoice line pairs with one PO line: same SKU first, then fuzzy description
   (confident pass at `description_min_score`, then a second pass at
   `description_fallback_score` for leftovers), one-to-one -> `line_not_on_po`
4. per paired line: qty <= PO qty minus qty on earlier invoices -> `qty_exceeds_remaining`;
   unit price within +/- `unit_price_pct` -> `unit_price_out_of_tolerance`
5. invoice total <= PO total minus earlier invoice totals (+ amount tolerance)
   -> `total_exceeds_remaining`

Status: any issue -> mismatch; else matched if no PO quantity remains open, else partial.
`po_not_found` is decided by the caller (the PO lookup), not here.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz

from docagent_schemas import (
    InvoiceLine,
    InvoiceRecord,
    LineMatch,
    MatchIssue,
    MatchResult,
    MatchTolerances,
    PurchaseOrderRecord,
)

_LEGAL_FORMS = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|plc|llp|gmbh|ag|bv|b v|nv|co|corp|corporation|"
    r"company|pc|sa|sarl|srl)\b"
)
_SPELLING = {"colour": "color", "grey": "gray", "aluminium": "aluminum"}


def _norm_text(text: str) -> str:
    t = text.lower()
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)  # 20lb -> 20 lb, 1200x900mm -> 1200 x900 mm
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(_SPELLING.get(w, w) for w in t.split())


def _norm_company(name: str) -> str:
    return " ".join(_LEGAL_FORMS.sub(" ", _norm_text(name)).split())


def _norm_tax_id(tax_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", tax_id.upper())


def description_score(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(_norm_text(a), _norm_text(b)))


def same_vendor(inv: InvoiceRecord, po: PurchaseOrderRecord, min_score: float) -> bool:
    if inv.seller.tax_id and po.vendor.tax_id:
        return _norm_tax_id(inv.seller.tax_id) == _norm_tax_id(po.vendor.tax_id)
    if inv.seller.name and po.vendor.name:
        a, b = _norm_company(inv.seller.name), _norm_company(po.vendor.name)
        return fuzz.token_sort_ratio(a, b) >= min_score
    return False


@dataclass(frozen=True)
class _Pair:
    invoice_line: int
    po_line: int
    method: str
    score: float


def pair_lines(
    inv_lines: list[InvoiceLine], po: PurchaseOrderRecord, tol: MatchTolerances
) -> list[_Pair]:
    """One-to-one pairing of invoice lines to PO lines (unpaired invoice lines omitted)."""
    pairs: list[_Pair] = []
    free_inv = set(range(len(inv_lines)))
    free_po = set(range(len(po.line_items)))

    # 1) exact SKU
    for i in sorted(free_inv):
        sku = inv_lines[i].sku
        if not sku:
            continue
        for j in sorted(free_po):
            po_sku = po.line_items[j].sku
            if po_sku and po_sku.upper() == sku.upper():
                pairs.append(_Pair(i, j, "sku", 100.0))
                free_inv.discard(i)
                free_po.discard(j)
                break

    # 2) fuzzy description, best-first, confident threshold then fallback threshold
    for threshold in (tol.description_min_score, tol.description_fallback_score):
        candidates = sorted(
            (
                (
                    description_score(
                        inv_lines[i].description or "", po.line_items[j].description or ""
                    ),
                    i,
                    j,
                )
                for i in free_inv
                for j in free_po
            ),
            key=lambda c: (-c[0], c[1], c[2]),
        )
        for score, i, j in candidates:
            if score < threshold:
                break
            if i in free_inv and j in free_po:
                pairs.append(_Pair(i, j, "description", round(score, 1)))
                free_inv.discard(i)
                free_po.discard(j)
    return sorted(pairs, key=lambda p: p.invoice_line)


def _po_line_qty_used(
    prior: list[InvoiceRecord], po: PurchaseOrderRecord, tol: MatchTolerances
) -> list[Decimal]:
    used = [Decimal(0)] * len(po.line_items)
    for inv in prior:
        for p in pair_lines(inv.line_items, po, tol):
            used[p.po_line] += inv.line_items[p.invoice_line].quantity or Decimal(0)
    return used


def match_invoice_to_po(
    invoice: InvoiceRecord,
    purchase_order: PurchaseOrderRecord,
    prior_invoices: list[InvoiceRecord] | None = None,
    tolerances: MatchTolerances | None = None,
) -> MatchResult:
    tol = tolerances or MatchTolerances()
    prior = prior_invoices or []
    po = purchase_order
    issues: list[MatchIssue] = []

    if not same_vendor(invoice, po, tol.description_min_score):
        issues.append(
            MatchIssue(
                rule="vendor_mismatch",
                message=f"invoice is from {invoice.seller.name!r} ({invoice.seller.tax_id}); "
                f"PO {po.po_number} was issued to {po.vendor.name!r} ({po.vendor.tax_id})",
            )
        )
    if invoice.currency and po.currency and invoice.currency != po.currency:
        issues.append(
            MatchIssue(
                rule="currency_mismatch",
                message=f"invoice currency {invoice.currency} differs from PO currency "
                f"{po.currency}",
            )
        )

    pairs = pair_lines(invoice.line_items, po, tol)
    paired = {p.invoice_line for p in pairs}
    for i, li in enumerate(invoice.line_items):
        if i not in paired:
            issues.append(
                MatchIssue(
                    rule="line_not_on_po",
                    invoice_line=i,
                    message=f"invoice line {i + 1} ({li.description!r}) has no matching PO line",
                )
            )

    used = _po_line_qty_used(prior, po, tol)
    remaining = [(li.quantity or Decimal(0)) - used[j] for j, li in enumerate(po.line_items)]
    for p in pairs:
        inv_line = invoice.line_items[p.invoice_line]
        po_line = po.line_items[p.po_line]
        qty = inv_line.quantity or Decimal(0)
        if qty > remaining[p.po_line]:
            issues.append(
                MatchIssue(
                    rule="qty_exceeds_remaining",
                    invoice_line=p.invoice_line,
                    po_line=p.po_line,
                    message=f"invoice line {p.invoice_line + 1} bills {qty}, but only "
                    f"{remaining[p.po_line]} of {po_line.quantity} remain on PO line "
                    f"{p.po_line + 1}",
                )
            )
        if inv_line.unit_price is not None and po_line.unit_price:
            pct = (inv_line.unit_price - po_line.unit_price) / po_line.unit_price * 100
            if abs(pct) > tol.unit_price_pct:
                issues.append(
                    MatchIssue(
                        rule="unit_price_out_of_tolerance",
                        invoice_line=p.invoice_line,
                        po_line=p.po_line,
                        message=f"invoice line {p.invoice_line + 1} unit price "
                        f"{inv_line.unit_price} vs PO {po_line.unit_price} ({pct:+.1f}%, "
                        f"tolerance +/-{tol.unit_price_pct}%)",
                    )
                )
        remaining[p.po_line] -= qty

    if invoice.total is not None and po.total is not None:
        remaining_value = po.total - sum((i.total or Decimal(0) for i in prior), Decimal(0))
        if invoice.total > remaining_value + tol.amount:
            issues.append(
                MatchIssue(
                    rule="total_exceeds_remaining",
                    message=f"invoice total {invoice.total} exceeds the PO's remaining value "
                    f"{remaining_value} ({po.total} less {len(prior)} earlier invoice(s))",
                )
            )

    open_qty = any(q > 0 for q in remaining)
    status = "mismatch" if issues else ("partial" if open_qty else "matched")
    return MatchResult(
        status=status,
        issues=issues,
        line_matches=[
            LineMatch(
                invoice_line=p.invoice_line,
                po_line=p.po_line,
                method="sku" if p.method == "sku" else "description",
                score=p.score,
            )
            for p in pairs
        ],
    )

"""Every validation rule, triggered by mutating the public sample pair, plus a dataset-wide
check that only the injected arithmetic defects are flagged."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from docagent_mcp_validation.rules import RULES, list_rules, validate
from docagent_mcp_validation.settings import ValidationSettings
from docagent_schemas import (
    ExtractedBuyer,
    ExtractedInvoiceLine,
    ExtractedSeller,
    Field,
    GroundTruth,
    Invoice,
    InvoiceRecord,
    PurchaseOrderRecord,
)

Pair = tuple[PurchaseOrderRecord, InvoiceRecord]


def rules_hit(doc_type: Any, record: Any, settings: ValidationSettings) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for issue in validate(doc_type, record, settings).issues:
        out.setdefault(issue.rule, []).append(issue.field_path)
    return out


def test_clean_sample_pair_passes(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = drift_pair
    for result in (validate("purchase_order", po, settings), validate("invoice", inv, settings)):
        assert result.valid and result.issues == [], result.issues


# -- one test per rule ------------------------------------------------------------------


def test_required_field(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = drift_pair
    bad_inv = inv.model_copy(update={"invoice_number": None, "total": None})
    assert rules_hit("invoice", bad_inv, settings)["required_field"] == [
        "invoice_number",
        "total",
    ]
    bad_po = po.model_copy(update={"vendor": po.vendor.model_copy(update={"name": " "})})
    assert rules_hit("purchase_order", bad_po, settings)["required_field"] == ["vendor.name"]


def test_line_items_present(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    assert "line_items_present" in rules_hit(
        "invoice", inv.model_copy(update={"line_items": []}), settings
    )


def test_currency_iso4217(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    assert rules_hit("invoice", inv.model_copy(update={"currency": "UKP"}), settings)[
        "currency_iso4217"
    ] == ["currency"]


def test_line_amount(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    lines = list(inv.line_items)
    lines[1] = lines[1].model_copy(update={"quantity": Decimal(41)})
    hit = rules_hit("invoice", inv.model_copy(update={"line_items": lines}), settings)
    assert hit == {"line_amount": ["line_items[1].line_total"]}


def test_line_amount_allows_one_cent_rounding(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    _, inv = drift_pair
    lines = list(inv.line_items)
    lines[0] = lines[0].model_copy(update={"line_total": lines[0].line_total + Decimal("0.01")})  # type: ignore[operator]
    hit = rules_hit("invoice", inv.model_copy(update={"line_items": lines}), settings)
    assert "line_amount" not in hit


def test_lines_sum_to_subtotal(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    bad = inv.model_copy(update={"subtotal": inv.subtotal + Decimal(10)})  # type: ignore[operator]
    hit = rules_hit("invoice", bad, settings)
    assert hit["lines_sum_to_subtotal"] == ["subtotal"]


def test_subtotal_plus_tax_equals_total(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, _ = drift_pair
    bad = po.model_copy(update={"total": po.total - Decimal("0.90")})  # type: ignore[operator]
    assert rules_hit("purchase_order", bad, settings) == {
        "subtotal_plus_tax_equals_total": ["total"]
    }


def test_due_date_not_before_invoice_date(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    bad = inv.model_copy(update={"due_date": inv.invoice_date - timedelta(days=1)})  # type: ignore[operator]
    assert rules_hit("invoice", bad, settings) == {"due_date_not_before_invoice_date": ["due_date"]}


def test_invoice_po_reference_format_is_a_warning(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    _, inv = drift_pair
    result = validate("invoice", inv.model_copy(update={"po_number": "85044"}), settings)
    assert [(i.rule, i.severity) for i in result.issues] == [
        ("invoice_po_reference_format", "warning")
    ]
    assert result.valid  # warnings never block


def test_delivery_date_not_before_po_date(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, _ = drift_pair
    bad = po.model_copy(update={"requested_delivery_date": po.po_date - timedelta(days=3)})  # type: ignore[operator]
    assert rules_hit("purchase_order", bad, settings) == {
        "delivery_date_not_before_po_date": ["requested_delivery_date"]
    }


@pytest.mark.parametrize("number", ["85044", "PO 85044", "PO-12", "PO-1234567"])
def test_po_number_format(number: str, drift_pair: Pair, settings: ValidationSettings) -> None:
    po, _ = drift_pair
    assert "po_number_format" in rules_hit(
        "purchase_order", po.model_copy(update={"po_number": number}), settings
    )


@pytest.mark.parametrize(
    ("terms", "ok"),
    [
        ("Net 30", True),
        ("net 45 days", True),
        ("2/10 Net 30", True),
        ("30 days", True),
        ("Due on receipt", True),
        ("whenever", False),
        ("Net thirty", False),
    ],
)
def test_payment_terms_parseable(
    terms: str, ok: bool, drift_pair: Pair, settings: ValidationSettings
) -> None:
    po, _ = drift_pair
    result = validate("purchase_order", po.model_copy(update={"payment_terms": terms}), settings)
    assert (not result.issues) == ok
    if not ok:
        assert result.issues[0].severity == "review" and not result.valid


def test_no_duplicate_skus(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, _ = drift_pair
    lines = list(po.line_items)
    lines[1] = lines[1].model_copy(update={"sku": lines[0].sku})
    assert rules_hit("purchase_order", po.model_copy(update={"line_items": lines}), settings) == {
        "no_duplicate_skus": ["line_items[1].sku"]
    }


def _wrap(value: Any, conf: float = 0.95) -> Field[Any]:
    return Field[Any](value=value, confidence=conf, source_page=1)


def _extracted(inv: InvoiceRecord, low: dict[str, float]) -> Invoice:
    def f(path: str, value: Any) -> Field[Any]:
        return _wrap(value, low.get(path, 0.95))

    return Invoice.model_validate(
        {
            "invoice_number": f("invoice_number", inv.invoice_number),
            "invoice_date": f("invoice_date", inv.invoice_date),
            "due_date": f("due_date", inv.due_date),
            "currency": f("currency", inv.currency),
            "po_number": f("po_number", inv.po_number),
            "seller": ExtractedSeller(
                name=f("seller.name", inv.seller.name),
                address=f("seller.address", inv.seller.address),
                tax_id=f("seller.tax_id", inv.seller.tax_id),
            ),
            "buyer": ExtractedBuyer(
                name=f("buyer.name", inv.buyer.name), address=f("buyer.address", inv.buyer.address)
            ),
            "line_items": [
                ExtractedInvoiceLine(
                    sku=f(f"line_items[{i}].sku", li.sku),
                    description=f(f"line_items[{i}].description", li.description),
                    quantity=f(f"line_items[{i}].quantity", float(li.quantity or 0)),
                    unit_price=f(f"line_items[{i}].unit_price", float(li.unit_price or 0)),
                    line_total=f(f"line_items[{i}].line_total", float(li.line_total or 0)),
                )
                for i, li in enumerate(inv.line_items)
            ],
            "subtotal": f("subtotal", float(inv.subtotal or 0)),
            "tax_amount": f("tax_amount", float(inv.tax_amount or 0)),
            "total": f("total", float(inv.total or 0)),
        }
    )


def test_low_confidence(drift_pair: Pair, settings: ValidationSettings) -> None:
    _, inv = drift_pair
    extracted = _extracted(inv, {"line_items[1].unit_price": 0.55, "seller.tax_id": 0.79})
    result = validate("invoice", extracted, settings)
    assert {(i.rule, i.field_path, i.severity) for i in result.issues} == {
        ("low_confidence", "line_items[1].unit_price", "review"),
        ("low_confidence", "seller.tax_id", "review"),
    }
    assert not result.valid
    assert validate("invoice", _extracted(inv, {}), settings).issues == []


def test_record_of_the_wrong_type_is_rejected(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    po, _ = drift_pair
    with pytest.raises(TypeError):
        validate("invoice", po, settings)


def test_every_rule_has_a_test_above() -> None:
    tested = {
        "required_field", "line_items_present", "currency_iso4217", "line_amount",
        "lines_sum_to_subtotal", "subtotal_plus_tax_equals_total",
        "due_date_not_before_invoice_date", "invoice_po_reference_format",
        "delivery_date_not_before_po_date", "po_number_format", "payment_terms_parseable",
        "no_duplicate_skus", "low_confidence",
    }  # fmt: skip
    assert {r.id for r in RULES} == tested


def test_list_rules_filters_by_doc_type() -> None:
    inv_rules = {r.rule for r in list_rules("invoice")}
    po_rules = {r.rule for r in list_rules("purchase_order")}
    assert "due_date_not_before_invoice_date" in inv_rules - po_rules
    assert "no_duplicate_skus" in po_rules - inv_rules
    assert "line_amount" in inv_rules & po_rules


# -- whole dataset ------------------------------------------------------------------------

_DEFECT_RULE = {
    "arithmetic_total": "subtotal_plus_tax_equals_total",
    "arithmetic_line": "line_amount",
}


def test_dataset_only_injected_defects_fail(
    truths: list[GroundTruth], settings: ValidationSettings
) -> None:
    for gt in truths:
        expected = {_DEFECT_RULE[i.kind] for i in gt.injected if i.kind in _DEFECT_RULE}
        got = set(rules_hit(gt.doc_type, gt.record, settings))
        assert got == expected, gt.doc_id

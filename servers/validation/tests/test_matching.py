"""match_invoice_to_po: the required sample-pair outcomes, one test per issue rule, and the
whole dataset against its expected outcomes."""

from decimal import Decimal

from docagent_mcp_validation.matching import match_invoice_to_po, pair_lines
from docagent_mcp_validation.settings import ValidationSettings
from docagent_schemas import (
    GroundTruth,
    InvoiceLine,
    InvoiceRecord,
    MatchResult,
    PurchaseOrderRecord,
)

Pair = tuple[PurchaseOrderRecord, InvoiceRecord]


def _rules(result: MatchResult) -> list[str]:
    return sorted(i.rule for i in result.issues)


def _match(
    po: PurchaseOrderRecord,
    inv: InvoiceRecord,
    s: ValidationSettings,
    prior: list[InvoiceRecord] | None = None,
) -> MatchResult:
    return match_invoice_to_po(inv, po, prior, s.match)


# -- required outcomes on the public sample pairs ----------------------------------------


def test_price_drift_sample_is_mismatch_with_exactly_two_rules(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    po, inv = drift_pair
    assert po.po_number == "PO-85044" and inv.po_number == "PO-85044"
    result = _match(po, inv, settings)
    assert result.status == "mismatch"
    assert _rules(result) == ["total_exceeds_remaining", "unit_price_out_of_tolerance"]
    [price] = [i for i in result.issues if i.rule == "unit_price_out_of_tolerance"]
    assert (price.invoice_line, price.po_line) == (1, 1)
    assert "+5.0%" in price.message


def test_po_1042_sample_is_partial(partial_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = partial_pair
    assert po.po_number == "PO-1042"
    result = _match(po, inv, settings)
    assert (result.status, result.issues) == ("partial", [])


def test_full_sample_is_matched(full_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = full_pair
    result = _match(po, inv, settings)
    assert (result.status, result.issues) == ("matched", [])
    assert len(result.line_matches) == len(inv.line_items)


# -- one test per issue rule (mutating the drift pair) ----------------------------------------


def _undrifted(pair: Pair) -> Pair:
    """The drift pair with the invoice's prices put back to the PO's (a clean full invoice)."""
    po, inv = pair
    lines = [
        li.model_copy(
            update={
                "unit_price": po.line_items[i].unit_price,
                "line_total": po.line_items[i].line_total,
            }
        )
        for i, li in enumerate(inv.line_items)
    ]
    return po, inv.model_copy(
        update={
            "line_items": lines,
            "subtotal": po.subtotal,
            "tax_amount": po.tax_amount,
            "total": po.total,
        }
    )


def test_clean_full_invoice_matches(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    assert _match(po, inv, settings).status == "matched"


def test_vendor_mismatch_by_tax_id(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    other = inv.seller.model_copy(
        update={"name": "Oakhaven Workplace Supplies Ltd", "tax_id": "GB739160524"}
    )
    result = _match(po, inv.model_copy(update={"seller": other}), settings)
    assert _rules(result) == ["vendor_mismatch"]


def test_vendor_name_variants_match_without_tax_id(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    po, inv = _undrifted(drift_pair)
    seller = inv.seller.model_copy(
        update={"name": "BRIGHTLEDGER STATIONERS LIMITED", "tax_id": None}
    )
    assert _match(po, inv.model_copy(update={"seller": seller}), settings).issues == []


def test_currency_mismatch(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    assert _rules(_match(po, inv.model_copy(update={"currency": "EUR"}), settings)) == [
        "currency_mismatch"
    ]


def test_line_not_on_po(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    extra = InvoiceLine(
        sku=None,
        description="Espresso machine, bean to cup",
        quantity=Decimal(1),
        unit_price=Decimal("0.01"),
        line_total=Decimal("0.01"),
    )
    result = _match(po, inv.model_copy(update={"line_items": [*inv.line_items, extra]}), settings)
    assert _rules(result) == ["line_not_on_po"]
    assert result.issues[0].invoice_line == 2


def test_qty_exceeds_remaining_after_earlier_invoice(
    drift_pair: Pair, settings: ValidationSettings
) -> None:
    po, inv = _undrifted(drift_pair)
    earlier = inv.model_copy(
        update={
            "line_items": [
                inv.line_items[0].model_copy(
                    update={"quantity": Decimal(1), "line_total": inv.line_items[0].unit_price}
                )
            ],
            "total": Decimal(1),
        }
    )
    result = _match(po, inv, settings, prior=[earlier])
    # Billing the full order again after one unit was invoiced over-runs quantity and value.
    assert _rules(result) == ["qty_exceeds_remaining", "total_exceeds_remaining"]
    [qty] = [i for i in result.issues if i.rule == "qty_exceeds_remaining"]
    assert (qty.invoice_line, qty.po_line) == (0, 0)
    assert "only 23 of 24 remain" in qty.message


def test_unit_price_within_tolerance_passes(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    cheaper = [
        inv.line_items[0].model_copy(
            update={"unit_price": (inv.line_items[0].unit_price or Decimal(0)) * Decimal("0.981")}
        )
    ]
    partial = inv.model_copy(update={"line_items": cheaper, "total": Decimal(1)})
    assert _match(po, partial, settings).status == "partial"


def test_total_exceeds_remaining(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    result = _match(po, inv.model_copy(update={"total": po.total + Decimal("0.02")}), settings)  # type: ignore[operator]
    assert _rules(result) == ["total_exceeds_remaining"]


def test_total_within_one_cent_tolerance(drift_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = _undrifted(drift_pair)
    result = _match(po, inv.model_copy(update={"total": po.total + Decimal("0.01")}), settings)  # type: ignore[operator]
    assert result.status == "matched"


def test_sku_pairing_beats_description(full_pair: Pair, settings: ValidationSettings) -> None:
    po, inv = full_pair
    by_sku = [lm for lm in _match(po, inv, settings).line_matches if lm.method == "sku"]
    assert all(lm.score == 100 for lm in by_sku)


# -- the whole dataset ------------------------------------------------------------------------


def test_dataset_outcomes_match_ground_truth(
    truths: list[GroundTruth], settings: ValidationSettings
) -> None:
    pos = {
        t.record.po_number: t.record for t in truths if isinstance(t.record, PurchaseOrderRecord)
    }
    prior: dict[str, list[InvoiceRecord]] = {}
    checked = 0
    for gt in truths:
        if not isinstance(gt.record, InvoiceRecord) or gt.expected_match is None:
            continue
        if gt.expected_match.status == "po_not_found":
            assert gt.record.po_number not in pos
            continue
        po = pos[gt.record.po_number]
        result = _match(po, gt.record, settings, prior.get(po.po_number or ""))
        prior.setdefault(po.po_number or "", []).append(gt.record)
        assert result.status == gt.expected_match.status, gt.doc_id
        assert _rules(result) == sorted(gt.expected_match.rules), gt.doc_id
        checked += 1
    assert checked == 26


def test_every_line_pairs_with_the_right_product(
    truths: list[GroundTruth], settings: ValidationSettings
) -> None:
    from evals.generator.catalog import PRODUCTS

    po_wording = {p.invoice_description: p.description for p in PRODUCTS}
    pos = {
        t.record.po_number: t.record for t in truths if isinstance(t.record, PurchaseOrderRecord)
    }
    pairs = 0
    for gt in truths:
        if not isinstance(gt.record, InvoiceRecord) or gt.record.po_number not in pos:
            continue
        po = pos[gt.record.po_number]
        found = pair_lines(gt.record.line_items, po, settings.match)
        assert len(found) == len(gt.record.line_items), gt.doc_id
        for p in found:
            desc = gt.record.line_items[p.invoice_line].description or ""
            assert po.line_items[p.po_line].description == po_wording[desc], gt.doc_id
            pairs += 1
    assert pairs == 107

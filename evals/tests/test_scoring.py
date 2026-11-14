from decimal import Decimal
from typing import Any

import pytest

from evals.scoring import DocOutcome, f1, policy_for, report, score_record, values_match


@pytest.mark.parametrize(
    ("path", "expected", "predicted", "ok"),
    [
        ("invoice_number", "BLS-2026-2422", "BLS-2026-2422", True),
        ("invoice_number", "INV004677", "INVQ04677", False),  # IDs are exact
        ("invoice_number", "PO-1", " PO-1 ", True),
        ("invoice_date", "2026-08-29", "2026-08-29", True),
        ("due_date", "2026-08-29", "2026-08-30", False),
        ("currency", "GBP", "gbp", False),
        ("total", "5890.66", "5890.660", True),
        ("line_items[1].quantity", "24", "24.0", True),
        ("line_items[1].unit_price", "98.08", "98.07", False),
        ("seller.name", "Pinewick Data Accessories Inc.", "PINEWICK DATA ACCESSORIES INC", True),
        ("seller.name", "Pinewick Data Accessories Inc.", "Finewick Data Accessories Inc.", True),
        ("seller.name", "Pinewick Data Accessories Inc.", "Brightledger Stationers Ltd", False),
        (
            "buyer.address",
            "9 Chandlers Wharf, Southampton SO14 3QB, United Kingdom",
            "9 Chandlers Wharf Southampton SO14 3QB United Kingdom",
            True,
        ),
        ("seller.tax_id", None, None, True),
        ("seller.tax_id", "GB1", None, False),
    ],
)
def test_match_policy(path: str, expected: Any, predicted: Any, ok: bool) -> None:
    assert values_match(path, expected, predicted) is ok


def test_every_record_field_has_a_policy() -> None:
    import json
    from pathlib import Path

    from evals.scoring import flatten

    root = Path(__file__).resolve().parents[1] / "dataset" / "samples"
    for name in ("po_001", "inv_001"):
        record = json.loads((root / f"{name}.json").read_text())["record"]
        for path in flatten(record):
            policy_for(path)  # raises for an unknown field


def test_score_counts_missing_and_extra_lines() -> None:
    expected = {"total": "10.00", "line_items": [{"sku": "A", "line_total": "10.00"}]}
    predicted = {
        "total": "10.00",
        "line_items": [{"sku": "A", "line_total": "10.00"}, {"sku": "X", "line_total": "1.00"}],
    }
    s = score_record(expected, predicted)
    assert (s.correct, s.total, s.tp, s.fp, s.fn) == (3, 3, 3, 2, 0)
    s = score_record(expected, None)  # failed run: everything wrong
    assert (s.correct, s.tp, s.fn) == (0, 0, 3)


def test_f1() -> None:
    assert f1(8, 2, 2) == {"precision": 0.8, "recall": 0.8, "f1": pytest.approx(0.8)}


def _outcome(doc_id: str, **kw: Any) -> DocOutcome:
    base: dict[str, Any] = dict(
        doc_id=doc_id,
        doc_type="invoice",
        scanned=False,
        variant_of=None,
        status="completed",
        predicted_doc_type="invoice",
        expected_record={"total": "1"},
        predicted_record={"total": "1"},
        expected_match="matched",
        expected_rules=[],
        predicted_match="matched",
        predicted_rules=[],
        injected=[],
        escalated=False,
        reviewed=False,
        cost_usd=Decimal("0.005"),
        latency_s=5.0,
    )
    base.update(kw)
    return DocOutcome(**base)


def test_report_metrics() -> None:
    outs = [
        _outcome("a"),
        _outcome(
            "b",
            expected_match="mismatch",
            predicted_match="mismatch",
            expected_rules=["unit_price_out_of_tolerance"],
            predicted_rules=["unit_price_out_of_tolerance"],
            injected=["price_drift"],
            escalated=True,
            latency_s=9.0,
        ),
        _outcome(
            "c",
            expected_match="mismatch",
            predicted_match="partial",
            injected=["over_billed_qty"],
            predicted_record={"total": "2"},
            reviewed=True,
        ),
        _outcome(
            "c_scan",
            variant_of="c",
            scanned=True,
            expected_match="mismatch",
            predicted_match="mismatch",
            injected=["over_billed_qty"],
        ),
    ]
    r = report(outs, [])
    assert r["field_accuracy"] == 0.75
    assert r["match_status_accuracy"] == 0.75
    assert r["mismatch_recall"] == pytest.approx(2 / 3)
    assert r["mismatch_recall_by_kind"]["over_billed_qty"] == 0.5
    assert r["clean_vs_scanned"]["paired"] == {
        "pairs": 1,
        "clean_twins": 0.0,
        "scanned_variants": 1.0,
    }
    assert (r["escalation_rate"], r["review_rate"]) == (0.25, 0.25)
    assert r["latency_s"]["p50"] == 5.0 and r["cost_per_doc_usd"]["max"] == 0.005

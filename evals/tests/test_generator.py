"""Synthetic dataset generator: determinism, consistency with the committed dataset,
ground-truth invariants and scenario composition."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from docagent_schemas import GroundTruth, InvoiceRecord, PurchaseOrderRecord
from evals.generator.catalog import BUYERS, VENDORS
from evals.generator.dataset import AMOUNT_TOLERANCE, DEFAULT_SEED, generate
from evals.generator.scan import page_texts

COMMITTED = Path(__file__).resolve().parents[1] / "dataset"


@pytest.fixture(scope="module")
def gen_a(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[GroundTruth]]:
    out = tmp_path_factory.mktemp("gen_a")
    return out, generate(out, DEFAULT_SEED)


@pytest.fixture(scope="module")
def gen_b(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("gen_b")
    generate(out, DEFAULT_SEED)
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and (p.name == "manifest.json" or p.parent.name in ("samples", "eval"))
    }


def test_same_seed_is_byte_identical(gen_a: tuple[Path, list[GroundTruth]], gen_b: Path) -> None:
    a, b = _files(gen_a[0]), _files(gen_b)
    assert a.keys() == b.keys()
    assert len([k for k in a if k.endswith(".pdf")]) == 71  # 60 documents + 11 scanned variants
    differing = [k for k in a if a[k] != b[k]]
    assert differing == []


def test_different_seed_differs(tmp_path: Path, gen_a: tuple[Path, list[GroundTruth]]) -> None:
    generate(tmp_path, DEFAULT_SEED + 1)
    assert (tmp_path / "manifest.json").read_bytes() != (gen_a[0] / "manifest.json").read_bytes()


def test_committed_dataset_matches_generator(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    """Ground truth and manifest in git must be what the generator produces for DEFAULT_SEED."""
    fresh = {k: v for k, v in _files(gen_a[0]).items() if k.endswith(".json")}
    committed = {k: v for k, v in _files(COMMITTED).items() if k.endswith(".json")}
    assert committed.keys() == fresh.keys(), "run `uv run python -m evals.generator`"
    stale = [k for k in fresh if fresh[k] != committed[k]]
    assert stale == [], "committed dataset is stale: run `uv run python -m evals.generator`"


def test_composition(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    variants = [t for t in gen_a[1] if t.variant_of is not None]
    truths = [t for t in gen_a[1] if t.variant_of is None]
    assert Counter((t.split, t.doc_type) for t in truths) == {
        ("sample", "purchase_order"): 3,
        ("sample", "invoice"): 3,
        ("eval", "purchase_order"): 27,
        ("eval", "invoice"): 27,
    }
    invoices = [t for t in truths if t.doc_type == "invoice"]
    statuses = Counter(t.expected_match.status if t.expected_match else None for t in invoices)
    assert statuses == {"matched": 12, "partial": 7, "mismatch": 7, "po_not_found": 2, None: 2}
    problem_share = (statuses["mismatch"] + statuses["po_not_found"]) / len(invoices)
    assert 0.25 <= problem_share <= 0.35  # "~30% with injected mismatches"

    kinds = Counter(i.kind for t in truths for i in t.injected)
    assert kinds == {
        "price_drift": 3,
        "over_billed_qty": 2,
        "wrong_vendor": 2,
        "missing_po": 2,
        "arithmetic_total": 2,
        "arithmetic_line": 1,
    }
    for doc_type in ("purchase_order", "invoice"):
        templates = Counter(t.template for t in truths if t.doc_type == doc_type)
        assert set(templates) == {"classic", "modern", "compact"}
        assert min(templates.values()) >= 3
    # Scanning: 11 eval documents have a scanned variant; the sample inv_002 is scanned itself.
    assert [t.doc_id for t in truths if t.scanned] == ["inv_002"]
    assert Counter(v.doc_type for v in variants) == {"purchase_order": 5, "invoice": 6}
    by_id = {t.doc_id: t for t in truths}
    for v in variants:
        twin = by_id[v.variant_of or ""]
        assert v.scanned and not twin.scanned and v.split == twin.split == "eval"
        assert v.record == twin.record and v.injected == twin.injected
        assert v.expected_match == twin.expected_match
        assert v.doc_id == f"{twin.doc_id}_scan"
    assert any(t.pages > 1 for t in truths)
    assert all(t.pages <= 5 for t in truths)


def test_samples_are_self_contained_with_one_mismatch(
    gen_a: tuple[Path, list[GroundTruth]],
) -> None:
    samples = [t for t in gen_a[1] if t.split == "sample"]
    po_ids = {t.doc_id for t in samples if t.doc_type == "purchase_order"}
    invs = [t for t in samples if t.doc_type == "invoice"]
    assert {t.references_po for t in invs} == po_ids
    assert [t.expected_match.status for t in invs if t.expected_match] == [
        "matched",
        "partial",
        "mismatch",
    ]
    po_numbers = {t.record.po_number for t in samples if isinstance(t.record, PurchaseOrderRecord)}
    assert "PO-1042" in po_numbers  # used by the example questions in SPEC §2


def _arith_ok(rec: InvoiceRecord | PurchaseOrderRecord) -> tuple[bool, bool]:
    """(every line qty x price == line_total, subtotal + tax == total and lines sum)."""
    lines_ok = all(
        li.quantity is not None
        and li.unit_price is not None
        and abs(li.quantity * li.unit_price - (li.line_total or Decimal(0))) <= AMOUNT_TOLERANCE
        for li in rec.line_items
    )
    s = sum((li.line_total or Decimal(0) for li in rec.line_items), Decimal(0))
    totals_ok = (
        rec.subtotal == s
        and rec.tax_amount is not None
        and rec.total == (rec.subtotal or Decimal(0)) + rec.tax_amount
    )
    return lines_ok, totals_ok


def test_arithmetic_is_consistent_except_where_injected(
    gen_a: tuple[Path, list[GroundTruth]],
) -> None:
    for t in gen_a[1]:
        kinds = {i.kind for i in t.injected}
        lines_ok, totals_ok = _arith_ok(t.record)
        assert lines_ok == ("arithmetic_line" not in kinds), t.doc_id
        assert totals_ok == ("arithmetic_total" not in kinds), t.doc_id


def test_invoices_reference_earlier_pos_in_same_split(
    gen_a: tuple[Path, list[GroundTruth]],
) -> None:
    order = {t.doc_id: i for i, t in enumerate(gen_a[1])}
    by_id = {t.doc_id: t for t in gen_a[1]}
    for t in gen_a[1]:
        if t.doc_type != "invoice" or t.references_po is None:
            continue
        po = by_id[t.references_po]
        assert order[po.doc_id] < order[t.doc_id]
        assert po.split == t.split
        assert isinstance(t.record, InvoiceRecord) and isinstance(po.record, PurchaseOrderRecord)
        assert t.record.po_number == po.record.po_number
        assert t.record.currency == po.record.currency


def test_missing_po_cites_unknown_number(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    known = {t.record.po_number for t in gen_a[1] if isinstance(t.record, PurchaseOrderRecord)}
    missing = [
        t
        for t in gen_a[1]
        if t.variant_of is None and any(i.kind == "missing_po" for i in t.injected)
    ]
    assert len(missing) == 2
    for t in missing:
        assert isinstance(t.record, InvoiceRecord)
        assert t.record.po_number and t.record.po_number not in known


def test_only_fictional_catalog_companies_appear(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    names = {c.name for c in (*VENDORS, *BUYERS)}
    for t in gen_a[1]:
        r = t.record
        parties = (
            [r.seller.name, r.buyer.name]
            if isinstance(r, InvoiceRecord)
            else [r.vendor.name, r.buyer.name]
        )
        assert set(parties) <= names, t.doc_id


def test_text_layer_present_only_on_clean_pdfs(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    root, truths = gen_a
    for t in truths:
        text = "".join(page_texts((root / t.file).read_bytes()))
        number = (
            t.record.invoice_number if isinstance(t.record, InvoiceRecord) else t.record.po_number
        )
        if t.scanned:
            assert text.strip() == "", t.doc_id
        else:
            assert number and number in text, t.doc_id
            assert "fictional" in text


def test_manifest_lists_pos_before_invoices(gen_a: tuple[Path, list[GroundTruth]]) -> None:
    manifest = json.loads((gen_a[0] / "manifest.json").read_text(encoding="utf-8"))
    types = [d["doc_type"] for d in manifest["documents"]]
    assert types == sorted(types, key=lambda x: x != "purchase_order")
    assert manifest["seed"] == DEFAULT_SEED

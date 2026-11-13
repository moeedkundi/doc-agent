from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from docagent_schemas import (
    Buyer,
    ExtractedBuyer,
    ExtractedInvoiceLine,
    ExtractedSeller,
    Field,
    GroundTruth,
    Invoice,
    InvoiceLine,
    InvoiceRecord,
    PurchaseOrderLine,
    PurchaseOrderRecord,
    Seller,
    Vendor,
)
from docagent_schemas.json_schema import DEFAULT_DIR, render_all


def f[T](value: T | None, confidence: float = 0.9) -> Field[T]:
    return Field[T](value=value, confidence=confidence, source_page=1, evidence=None)


def test_field_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Field[str](value="x", confidence=1.5)
    with pytest.raises(ValidationError):
        Field[str](value="x", confidence=-0.1)


def test_field_source_page_is_one_based() -> None:
    with pytest.raises(ValidationError):
        Field[str](value="x", confidence=0.5, source_page=0)


def test_extracted_invoice_converts_to_record_with_decimals() -> None:
    inv = Invoice(
        invoice_number=f("INV-1"),
        invoice_date=f(date(2026, 3, 1)),
        due_date=f(date(2026, 3, 31)),
        currency=f("USD"),
        po_number=f(None, 0.2),
        seller=ExtractedSeller(name=f("Acme"), address=f("1 Road"), tax_id=f("T1")),
        buyer=ExtractedBuyer(name=f("Buyer Co"), address=f("2 Street")),
        line_items=[
            ExtractedInvoiceLine(
                sku=f(None),
                description=f("Paper"),
                quantity=f(3.0),
                unit_price=f(4.1),
                line_total=f(12.3),
            )
        ],
        subtotal=f(12.3),
        tax_amount=f(0.985),
        total=f(13.29),
    )
    rec = inv.to_record()
    assert rec.line_items[0].quantity == Decimal("3")
    assert str(rec.line_items[0].quantity) == "3"
    assert rec.line_items[0].unit_price == Decimal("4.10")
    assert rec.tax_amount == Decimal("0.98")  # banker's rounding of 0.985
    assert rec.po_number is None


def test_records_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Buyer.model_validate({"name": "a", "address": "b", "vat": "c"})


def _po_record() -> PurchaseOrderRecord:
    return PurchaseOrderRecord(
        po_number="PO-1042",
        po_date=date(2026, 2, 1),
        currency="EUR",
        payment_terms="Net 30",
        buyer=Buyer(name="B", address="addr"),
        vendor=Vendor(name="V", address="addr", tax_id="DE123"),
        ship_to="dock 4",
        requested_delivery_date=date(2026, 2, 15),
        line_items=[
            PurchaseOrderLine(
                sku="SKU-1",
                description="Toner",
                quantity=Decimal(2),
                unit_price=Decimal("10.00"),
                line_total=Decimal("20.00"),
            )
        ],
        subtotal=Decimal("20.00"),
        tax_amount=Decimal("3.80"),
        total=Decimal("23.80"),
    )


def test_ground_truth_round_trips_and_keeps_record_type() -> None:
    inv = InvoiceRecord(
        invoice_number="INV-9",
        invoice_date=date(2026, 3, 1),
        due_date=date(2026, 3, 31),
        currency="EUR",
        po_number="PO-1042",
        seller=Seller(name="V", address="addr", tax_id="DE123"),
        buyer=Buyer(name="B", address="addr"),
        line_items=[
            InvoiceLine(
                sku=None,
                description="Toner",
                quantity=Decimal(2),
                unit_price=Decimal("10.00"),
                line_total=Decimal("20.00"),
            )
        ],
        subtotal=Decimal("20.00"),
        tax_amount=Decimal("3.80"),
        total=Decimal("23.80"),
    )
    for rec, doc_type in ((inv, "invoice"), (_po_record(), "purchase_order")):
        gt = GroundTruth.model_validate(
            {
                "doc_id": "x",
                "doc_type": doc_type,
                "file": "x.pdf",
                "split": "eval",
                "template": "t",
                "scanned": False,
                "pages": 1,
                "record": rec.model_dump(mode="json"),
            }
        )
        back = GroundTruth.model_validate_json(gt.model_dump_json())
        assert type(back.record) is type(rec)
        assert back.record == rec


def test_committed_json_schemas_are_up_to_date() -> None:
    rendered = render_all()
    committed = {p.name: p.read_text(encoding="utf-8") for p in DEFAULT_DIR.glob("*.json")}
    assert committed == rendered, (
        "JSON schemas are stale: run `uv run python -m docagent_schemas.json_schema`"
    )

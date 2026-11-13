"""Extraction output: the same shape as `records.py`, with every leaf wrapped in `Field`.

Numbers are floats here because that is what models emit; `to_record()` converts them to
`Decimal` rounded to cents (quantities keep up to 3 decimals).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from docagent_schemas.fields import Field
from docagent_schemas.records import (
    Buyer,
    InvoiceLine,
    InvoiceRecord,
    PurchaseOrderLine,
    PurchaseOrderRecord,
    Seller,
    Vendor,
)

_CENTS = Decimal("0.01")
_QTY = Decimal("0.001")


def _money(f: Field[float]) -> Decimal | None:
    return None if f.value is None else Decimal(str(f.value)).quantize(_CENTS)


def _qty(f: Field[float]) -> Decimal | None:
    if f.value is None:
        return None
    q = Decimal(str(f.value)).quantize(_QTY)
    return q.quantize(Decimal(1)) if q == q.to_integral_value() else q.normalize()


class _Extracted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractedSeller(_Extracted):
    name: Field[str]
    address: Field[str]
    tax_id: Field[str]

    def to_record(self) -> Seller:
        return Seller(name=self.name.value, address=self.address.value, tax_id=self.tax_id.value)


class ExtractedBuyer(_Extracted):
    name: Field[str]
    address: Field[str]

    def to_record(self) -> Buyer:
        return Buyer(name=self.name.value, address=self.address.value)


class ExtractedVendor(_Extracted):
    name: Field[str]
    address: Field[str]
    tax_id: Field[str]

    def to_record(self) -> Vendor:
        return Vendor(name=self.name.value, address=self.address.value, tax_id=self.tax_id.value)


class ExtractedInvoiceLine(_Extracted):
    sku: Field[str]
    description: Field[str]
    quantity: Field[float]
    unit_price: Field[float]
    line_total: Field[float]

    def to_record(self) -> InvoiceLine:
        return InvoiceLine(
            sku=self.sku.value,
            description=self.description.value,
            quantity=_qty(self.quantity),
            unit_price=_money(self.unit_price),
            line_total=_money(self.line_total),
        )


class ExtractedPurchaseOrderLine(_Extracted):
    sku: Field[str]
    description: Field[str]
    quantity: Field[float]
    unit_price: Field[float]
    line_total: Field[float]

    def to_record(self) -> PurchaseOrderLine:
        return PurchaseOrderLine(
            sku=self.sku.value,
            description=self.description.value,
            quantity=_qty(self.quantity),
            unit_price=_money(self.unit_price),
            line_total=_money(self.line_total),
        )


class Invoice(_Extracted):
    invoice_number: Field[str]
    invoice_date: Field[date]
    due_date: Field[date]
    currency: Field[str]
    po_number: Field[str]
    seller: ExtractedSeller
    buyer: ExtractedBuyer
    line_items: list[ExtractedInvoiceLine]
    subtotal: Field[float]
    tax_amount: Field[float]
    total: Field[float]

    def to_record(self) -> InvoiceRecord:
        return InvoiceRecord(
            invoice_number=self.invoice_number.value,
            invoice_date=self.invoice_date.value,
            due_date=self.due_date.value,
            currency=self.currency.value,
            po_number=self.po_number.value,
            seller=self.seller.to_record(),
            buyer=self.buyer.to_record(),
            line_items=[li.to_record() for li in self.line_items],
            subtotal=_money(self.subtotal),
            tax_amount=_money(self.tax_amount),
            total=_money(self.total),
        )


class PurchaseOrder(_Extracted):
    po_number: Field[str]
    po_date: Field[date]
    currency: Field[str]
    payment_terms: Field[str]
    buyer: ExtractedBuyer
    vendor: ExtractedVendor
    ship_to: Field[str]
    requested_delivery_date: Field[date]
    line_items: list[ExtractedPurchaseOrderLine]
    subtotal: Field[float]
    tax_amount: Field[float]
    total: Field[float]

    def to_record(self) -> PurchaseOrderRecord:
        return PurchaseOrderRecord(
            po_number=self.po_number.value,
            po_date=self.po_date.value,
            currency=self.currency.value,
            payment_terms=self.payment_terms.value,
            buyer=self.buyer.to_record(),
            vendor=self.vendor.to_record(),
            ship_to=self.ship_to.value,
            requested_delivery_date=self.requested_delivery_date.value,
            line_items=[li.to_record() for li in self.line_items],
            subtotal=_money(self.subtotal),
            tax_amount=_money(self.tax_amount),
            total=_money(self.total),
        )

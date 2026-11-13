"""Plain, fully-typed records: what gets saved to the DB and what ground truth looks like.

These carry no confidence/provenance. Extraction output (`extracted.py`) is converted into
these after validation and human review.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Seller(_Record):
    name: str | None
    address: str | None
    tax_id: str | None


class Buyer(_Record):
    name: str | None
    address: str | None


class Vendor(_Record):
    name: str | None
    address: str | None
    tax_id: str | None


class InvoiceLine(_Record):
    sku: str | None
    """Optional: some invoices quote the PO's SKU, others only a description."""
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None


class PurchaseOrderLine(_Record):
    sku: str | None
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None


class InvoiceRecord(_Record):
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    """ISO 4217 code; validity is checked by the validation server, not here."""
    po_number: str | None
    seller: Seller
    buyer: Buyer
    line_items: list[InvoiceLine]
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total: Decimal | None


class PurchaseOrderRecord(_Record):
    po_number: str | None
    po_date: date | None
    currency: str | None
    payment_terms: str | None
    buyer: Buyer
    vendor: Vendor
    ship_to: str | None
    """Ship-to address."""
    requested_delivery_date: date | None
    line_items: list[PurchaseOrderLine]
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total: Decimal | None

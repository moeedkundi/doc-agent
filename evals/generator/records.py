"""Build ground-truth records (POs and invoices) from catalog data and a seeded RNG."""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from docagent_schemas import (
    Buyer,
    InvoiceLine,
    InvoiceRecord,
    PurchaseOrderLine,
    PurchaseOrderRecord,
    Seller,
    Vendor,
)
from evals.generator.catalog import CURRENCY, PRICE_FACTOR, PRODUCTS, TAX_RATE, Company, Product
from evals.generator.money import q2


@dataclass(frozen=True)
class PoContext:
    """Everything about a PO that isn't in the record but the invoice/renderer needs."""

    record: PurchaseOrderRecord
    buyer: Company
    vendor: Company
    products: tuple[Product, ...]
    tax_rate: Decimal


def terms_days(terms: str) -> int:
    return int(terms.rsplit("Net", 1)[1].strip()) if "Net" in terms else 0


def _totals(line_totals: list[Decimal], tax_rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = q2(sum(line_totals, Decimal(0)))
    tax = q2(subtotal * tax_rate)
    return subtotal, tax, subtotal + tax


def make_po(
    rng: random.Random,
    *,
    po_number: str,
    buyer: Company,
    vendor: Company,
    po_date: date,
    payment_terms: str,
    n_lines: int,
) -> PoContext:
    currency = CURRENCY[vendor.country]
    tax_rate = TAX_RATE[vendor.country]
    products = tuple(rng.sample(PRODUCTS, n_lines))
    lines: list[PurchaseOrderLine] = []
    for p in products:
        qty = Decimal(rng.choice((1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 25, 30, 40, 50)))
        # Vendor-specific price: list price in local currency, +/- up to 8%.
        variance = Decimal(rng.randint(-80, 80)) / Decimal(1000)
        unit = q2(p.unit_price_usd * PRICE_FACTOR[currency] * (1 + variance))
        lines.append(
            PurchaseOrderLine(
                sku=p.sku,
                description=p.description,
                quantity=qty,
                unit_price=unit,
                line_total=q2(qty * unit),
            )
        )
    subtotal, tax, total = _totals([li.line_total or Decimal(0) for li in lines], tax_rate)
    record = PurchaseOrderRecord(
        po_number=po_number,
        po_date=po_date,
        currency=currency,
        payment_terms=payment_terms,
        buyer=Buyer(name=buyer.name, address=buyer.address),
        vendor=Vendor(name=vendor.name, address=vendor.address, tax_id=vendor.tax_id),
        ship_to=buyer.address,
        requested_delivery_date=po_date + timedelta(days=rng.randint(7, 30)),
        line_items=lines,
        subtotal=subtotal,
        tax_amount=tax,
        total=total,
    )
    return PoContext(
        record=record, buyer=buyer, vendor=vendor, products=products, tax_rate=tax_rate
    )


@dataclass(frozen=True)
class InvoiceLinePlan:
    """One invoice line derived from a PO line (index), with what to bill."""

    po_line: int
    quantity: Decimal
    unit_price: Decimal
    show_sku: bool


def make_invoice(
    *,
    invoice_number: str,
    invoice_date: date,
    po: PoContext,
    seller: Company,
    po_number: str | None,
    plan: list[InvoiceLinePlan],
) -> InvoiceRecord:
    po_rec = po.record
    terms = po_rec.payment_terms or "Net 30"
    lines: list[InvoiceLine] = []
    for item in plan:
        product = po.products[item.po_line]
        lines.append(
            InvoiceLine(
                sku=product.sku if item.show_sku else None,
                description=product.invoice_description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=q2(item.quantity * item.unit_price),
            )
        )
    subtotal, tax, total = _totals([li.line_total or Decimal(0) for li in lines], po.tax_rate)
    return InvoiceRecord(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=invoice_date + timedelta(days=terms_days(terms)),
        currency=po_rec.currency,
        po_number=po_number,
        seller=Seller(name=seller.name, address=seller.address, tax_id=seller.tax_id),
        buyer=Buyer(name=po.buyer.name, address=po.buyer.address),
        line_items=lines,
        subtotal=subtotal,
        tax_amount=tax,
        total=total,
    )

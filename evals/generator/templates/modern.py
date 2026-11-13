"""'modern' layout: big title top-left, issuer top-right, facts in a strip of small-caps
labels, unboxed parties, zebra table without SKUs on invoices, filled total bar."""

import io
from decimal import Decimal

from reportlab.lib.colors import HexColor, white

from evals.generator.money import fmt_amount, fmt_date, fmt_qty
from evals.generator.render import GREY, Column, Page, new_canvas, page_size_for
from evals.generator.templates.base import (
    FICTION_NOTE,
    InvoiceDoc,
    PoDoc,
    money,
    tax_id_label,
    tax_label,
)

NAME = "modern"
INVOICE_SHOWS_SKU: bool | None = False
"""This layout never prints item codes on invoices (forces description matching)."""

ZEBRA = HexColor("#f1f1ef")


def _issuer(p: Page, name: str, address_lines: tuple[str, ...], extra: str | None) -> None:
    x = p.width - p.margin
    p.text(x, p.y - 10, name, size=10, bold=True, align="right")
    y = p.y - 23
    for row in (*address_lines, *([extra] if extra else [])):
        p.text(x, y, row, size=8, color=GREY, align="right")
        y -= 10.5


def _facts(p: Page, facts: list[tuple[str, str]]) -> None:
    col_w = (p.width - 2 * p.margin) / max(len(facts), 4)
    x = p.margin
    for label, value in facts:
        p.text(x, p.y, label.upper(), size=6.5, bold=True, color=p.style.accent)
        p.text(x, p.y - 13, value, size=10)
        x += col_w
    p.y -= 26
    p.rule(p.y, color=p.style.accent, width=1.2)
    p.y -= 22


def _parties(p: Page, blocks: list[tuple[str, list[str]]]) -> None:
    col_w = (p.width - 2 * p.margin) / 2
    lowest = p.y
    for i, (label, rows) in enumerate(blocks):
        x = p.margin + i * col_w
        p.text(x, p.y, label.upper(), size=6.5, bold=True, color=p.style.accent)
        lowest = min(lowest, p.lines(x, p.y - 14, rows, size=9, leading=11.5, bold_first=True))
    p.y = lowest - 16


def _totals(p: Page, rows: list[tuple[str, str]]) -> None:
    p.ensure(30 + 16 * len(rows))
    x_label = p.width - p.margin - 220
    x_val = p.width - p.margin - 8
    p.y -= 16
    for label, value in rows[:-1]:
        p.text(x_label + 8, p.y, label, size=9, color=GREY)
        p.text(x_val, p.y, value, size=9, align="right")
        p.y -= 15
    label, value = rows[-1]
    p.c.setFillColor(p.style.accent)
    p.c.rect(x_label, p.y - 7, 220, 20, stroke=0, fill=1)
    p.text(x_label + 8, p.y, label, size=10, bold=True, color=white)
    p.text(x_val, p.y, value, size=10.5, bold=True, color=white, align="right")
    p.y -= 30


def render_po(doc: PoDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.buyer)
    p = Page(
        new_canvas(buf, (w, h), f"Purchase Order {r.po_number}"),
        st,
        w,
        h,
        footer_note=FICTION_NOTE,
    )
    m = p.margin
    p.text(m, p.y - 24, "Purchase Order", size=26, bold=True, color=st.accent)
    _issuer(p, doc.buyer.name, doc.buyer.address_lines, None)
    p.y -= 78
    _facts(
        p,
        [
            ("PO no.", r.po_number or ""),
            ("Order date", fmt_date(r.po_date, st.date_style) if r.po_date else ""),
            ("Terms", r.payment_terms or ""),
            (
                "Required by",
                fmt_date(r.requested_delivery_date, st.date_style)
                if r.requested_delivery_date
                else "",
            ),
        ],
    )
    _parties(
        p,
        [
            (
                "Supplier",
                [
                    doc.vendor.name,
                    *doc.vendor.address_lines,
                    f"{tax_id_label(doc.vendor)} {doc.vendor.tax_id}",
                ],
            ),
            ("Deliver to", [doc.buyer.name, *doc.buyer.address_lines]),
        ],
    )
    cols = p.fit_columns(
        [
            Column("Item", 95),
            Column("Description", 215),
            Column("Qty", 40, "right"),
            Column("Price", 70, "right"),
            Column("Total", 75, "right"),
        ]
    )
    rows = [
        [
            li.sku or "",
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        for li in r.line_items
    ]
    p.table(cols, rows, zebra=ZEBRA)
    _totals(
        p,
        [
            ("Net amount", money(r.subtotal, r.currency, st)),
            (tax_label(doc.vendor, doc.tax_rate), money(r.tax_amount, r.currency, st)),
            (f"Order total ({r.currency})", money(r.total, r.currency, st)),
        ],
    )
    p.ensure(50)
    p.lines(
        m,
        p.y,
        [
            f"Ordered by {doc.contact}. Invoices must reference {r.po_number}.",
            "Deliveries outside the required date require prior written approval.",
        ],
        size=8,
        color=GREY,
    )
    p.finish()
    return buf.getvalue(), p.pages


def render_invoice(doc: InvoiceDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.seller)
    p = Page(
        new_canvas(buf, (w, h), f"Invoice {r.invoice_number}"),
        st,
        w,
        h,
        footer_note=FICTION_NOTE,
    )
    m = p.margin
    p.text(m, p.y - 24, "Invoice", size=28, bold=True, color=st.accent)
    _issuer(
        p,
        doc.seller.name,
        doc.seller.address_lines,
        f"{tax_id_label(doc.seller)} {doc.seller.tax_id}",
    )
    p.y -= 88
    facts = [
        ("Invoice #", r.invoice_number or ""),
        ("Issued", fmt_date(r.invoice_date, st.date_style) if r.invoice_date else ""),
        ("Due", fmt_date(r.due_date, st.date_style) if r.due_date else ""),
    ]
    if r.po_number:
        facts.append(("Purchase order", r.po_number))
    _facts(p, facts)
    _parties(p, [("Billed to", [r.buyer.name or "", *doc.buyer.address_lines])])
    cols = p.fit_columns(
        [
            Column("Description", 290),
            Column("Qty", 45, "right"),
            Column("Price", 80, "right"),
            Column("Total", 80, "right"),
        ]
    )
    rows = [
        [
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        for li in r.line_items
    ]
    p.table(cols, rows, zebra=ZEBRA)
    _totals(
        p,
        [
            ("Subtotal", money(r.subtotal, r.currency, st)),
            (tax_label(doc.seller, doc.tax_rate), money(r.tax_amount, r.currency, st)),
            (f"Total due ({r.currency})", money(r.total, r.currency, st)),
        ],
    )
    p.ensure(40)
    p.lines(
        m,
        p.y,
        [f"Terms: {doc.payment_terms}. Late payments may incur interest as permitted by law."],
        size=8,
        color=GREY,
    )
    p.finish()
    return buf.getvalue(), p.pages

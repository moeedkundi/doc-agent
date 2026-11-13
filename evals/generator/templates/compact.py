"""'compact' layout: monospace, centred letterhead, boxed key/value grid, gridded line
table with item codes, plain totals ledger. Looks like an ERP printout."""

import io
from dataclasses import replace
from decimal import Decimal

from reportlab.lib.colors import HexColor

from evals.generator.money import fmt_amount, fmt_date, fmt_qty
from evals.generator.render import BLACK, GREY, Column, Page, Style, new_canvas, page_size_for
from evals.generator.templates.base import (
    FICTION_NOTE,
    InvoiceDoc,
    PoDoc,
    money,
    tax_id_label,
    tax_label,
)

NAME = "compact"
INVOICE_SHOWS_SKU: bool | None = True

HEADER_FILL = HexColor("#e4e4e4")


def _letterhead(p: Page, name: str, address_lines: tuple[str, ...], title: str) -> None:
    cx = p.width / 2
    p.text(cx, p.y - 12, name.upper(), size=12, bold=True, align="center")
    p.text(cx, p.y - 25, " / ".join(address_lines), size=7.5, color=GREY, align="center")
    p.y -= 40
    p.c.setStrokeColor(BLACK)
    p.c.setLineWidth(1)
    p.c.rect(p.margin, p.y - 20, p.width - 2 * p.margin, 20, stroke=1, fill=0)
    p.text(cx, p.y - 14, title, size=11, bold=True, align="center")
    p.y -= 30


def _grid(p: Page, left: list[tuple[str, str]], right_title: str, right: list[str]) -> None:
    """Two boxed panels: key/value facts left, a party block right."""
    lead = 11.0
    rows = max(len(left), len(right) + 1)
    h = rows * lead + 10
    half = (p.width - 2 * p.margin) / 2
    p.c.setStrokeColor(BLACK)
    p.c.setLineWidth(0.6)
    p.c.rect(p.margin, p.y - h, half, h, stroke=1, fill=0)
    p.c.rect(p.margin + half, p.y - h, half, h, stroke=1, fill=0)
    y = p.y - 12
    for label, value in left:
        p.text(p.margin + 6, y, f"{label:<14}", size=8)
        p.text(p.margin + 6 + 100, y, value, size=8, bold=True)
        y -= lead
    p.text(p.margin + half + 6, p.y - 12, right_title, size=8, bold=True)
    p.lines(p.margin + half + 6, p.y - 12 - lead, right, size=8, leading=lead)
    p.y -= h + 14


def _ledger(p: Page, rows: list[tuple[str, str]]) -> None:
    p.ensure(20 + 13 * len(rows))
    x_label = p.width - p.margin - 240
    x_val = p.width - p.margin - 4
    p.y -= 12
    for i, (label, value) in enumerate(rows):
        last = i == len(rows) - 1
        if last:
            p.text(x_label, p.y + 5, "=" * 34, size=8)
            p.y -= 6
        p.text(x_label, p.y, label, size=8.5, bold=last)
        p.text(x_val, p.y, value, size=8.5, bold=last, align="right")
        p.y -= 13
    p.y -= 10


def _mono(style: Style) -> Style:
    return replace(style, font="Courier", bold="Courier-Bold")


def render_po(doc: PoDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.buyer)
    p = Page(
        new_canvas(buf, (w, h), f"Purchase Order {r.po_number}"),
        _mono(st),
        w,
        h,
        footer_note=FICTION_NOTE,
    )
    _letterhead(p, doc.buyer.name, doc.buyer.address_lines, "PURCHASE ORDER")
    _grid(
        p,
        [
            ("PO NUMBER", r.po_number or ""),
            ("ORDER DATE", fmt_date(r.po_date, st.date_style) if r.po_date else ""),
            (
                "DELIVER BY",
                fmt_date(r.requested_delivery_date, st.date_style)
                if r.requested_delivery_date
                else "",
            ),
            ("TERMS", r.payment_terms or ""),
            ("CURRENCY", r.currency or ""),
        ],
        "VENDOR",
        [
            doc.vendor.name,
            *doc.vendor.address_lines,
            f"{tax_id_label(doc.vendor)}: {doc.vendor.tax_id}",
        ],
    )
    p.text(
        p.margin, p.y, "SHIP TO: " + ", ".join([doc.buyer.name, *doc.buyer.address_lines]), size=8
    )
    p.y -= 16
    cols = p.fit_columns(
        [
            Column("LN", 24),
            Column("ITEM CODE", 92),
            Column("DESCRIPTION", 200),
            Column("QTY", 40, "right"),
            Column("UNIT PRICE", 70, "right"),
            Column("EXTENDED", 75, "right"),
        ]
    )
    rows = [
        [
            f"{i + 1:02d}",
            li.sku or "",
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        for i, li in enumerate(r.line_items)
    ]
    p.table(cols, rows, size=7.5, header_fill=HEADER_FILL, grid=True)
    _ledger(
        p,
        [
            ("MERCHANDISE TOTAL", money(r.subtotal, r.currency, st)),
            (tax_label(doc.vendor, doc.tax_rate).upper(), money(r.tax_amount, r.currency, st)),
            ("PO TOTAL", money(r.total, r.currency, st)),
        ],
    )
    p.ensure(30)
    p.text(
        p.margin,
        p.y,
        f"BUYER: {doc.contact.upper()}   ** QUOTE PO NO. ON ALL INVOICES **",
        size=7.5,
    )
    p.finish()
    return buf.getvalue(), p.pages


def render_invoice(doc: InvoiceDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.seller)
    p = Page(
        new_canvas(buf, (w, h), f"Invoice {r.invoice_number}"),
        _mono(st),
        w,
        h,
        footer_note=FICTION_NOTE,
    )
    _letterhead(
        p,
        doc.seller.name,
        (*doc.seller.address_lines, f"{tax_id_label(doc.seller)} {doc.seller.tax_id}"),
        "INVOICE",
    )
    facts = [
        ("INVOICE NO", r.invoice_number or ""),
        ("INVOICE DATE", fmt_date(r.invoice_date, st.date_style) if r.invoice_date else ""),
        ("DUE DATE", fmt_date(r.due_date, st.date_style) if r.due_date else ""),
        ("CUST PO", r.po_number or "N/A"),
        ("CURRENCY", r.currency or ""),
    ]
    _grid(p, facts, "SOLD TO", [r.buyer.name or "", *doc.buyer.address_lines])
    cols = p.fit_columns(
        [
            Column("LN", 24),
            Column("ITEM CODE", 92),
            Column("DESCRIPTION", 200),
            Column("QTY", 40, "right"),
            Column("UNIT PRICE", 70, "right"),
            Column("AMOUNT", 75, "right"),
        ]
    )
    rows = [
        [
            f"{i + 1:02d}",
            li.sku or "",
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        for i, li in enumerate(r.line_items)
    ]
    p.table(cols, rows, size=7.5, header_fill=HEADER_FILL, grid=True)
    _ledger(
        p,
        [
            ("SUBTOTAL", money(r.subtotal, r.currency, st)),
            (tax_label(doc.seller, doc.tax_rate).upper(), money(r.tax_amount, r.currency, st)),
            ("INVOICE TOTAL", money(r.total, r.currency, st)),
        ],
    )
    p.ensure(30)
    p.text(
        p.margin, p.y, f"TERMS: {doc.payment_terms.upper()}  -  REMIT QUOTING INVOICE NO.", size=7.5
    )
    p.finish()
    return buf.getvalue(), p.pages

"""'classic' layout: company header left, document title and key facts right, boxed parties,
ruled line table, totals block bottom-right."""

import io
from decimal import Decimal

from reportlab.lib.colors import white

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

NAME = "classic"
INVOICE_SHOWS_SKU: bool | None = None
"""None: decided per invoice (some vendors print item codes, some do not)."""


def _party_box(p: Page, x: float, y: float, w: float, title: str, rows: list[str]) -> float:
    h = 22 + 12 * len(rows)
    p.c.setStrokeColor(GREY)
    p.c.setLineWidth(0.6)
    p.c.rect(x, y - h, w, h, stroke=1, fill=0)
    p.text(x + 8, y - 13, title, size=7.5, bold=True, color=p.style.accent)
    p.lines(x + 8, y - 26, rows, size=9, leading=12, bold_first=True)
    return y - h


def _totals(p: Page, rows: list[tuple[str, str]], *, bold_last: bool = True) -> None:
    x_label = p.width - p.margin - 210
    x_val = p.width - p.margin - 4
    p.y -= 14
    for i, (label, value) in enumerate(rows):
        last = bold_last and i == len(rows) - 1
        if last:
            p.rule(p.y + 10, x0=x_label, color=p.style.accent, width=1)
        p.text(x_label, p.y, label, size=10 if last else 9, bold=last)
        p.text(x_val, p.y, value, size=10 if last else 9, bold=last, align="right")
        p.y -= 15


def render_po(doc: PoDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.buyer)
    p = Page(
        new_canvas(buf, (w, h), f"Purchase Order {r.po_number}"), st, w, h, footer_note=FICTION_NOTE
    )
    m = p.margin

    p.text(m, p.y - 14, doc.buyer.name, size=15, bold=True, color=st.accent)
    p.lines(m, p.y - 30, list(doc.buyer.address_lines), size=8.5, color=GREY)
    p.text(w - m, p.y - 18, "PURCHASE ORDER", size=19, bold=True, align="right")
    facts = [
        ("PO Number", r.po_number or ""),
        ("PO Date", fmt_date(r.po_date, st.date_style) if r.po_date else ""),
        ("Payment Terms", r.payment_terms or ""),
        (
            "Deliver By",
            fmt_date(r.requested_delivery_date, st.date_style) if r.requested_delivery_date else "",
        ),
    ]
    fy = p.y - 40
    for label, value in facts:
        p.text(w - m - 190, fy, label, size=8.5, color=GREY)
        p.text(w - m, fy, value, size=9, bold=True, align="right")
        fy -= 13
    p.y = min(fy, p.y - 80) - 12

    box_w = (w - 2 * m - 16) / 2
    vendor_rows = [
        doc.vendor.name,
        *doc.vendor.address_lines,
        f"{tax_id_label(doc.vendor)}: {doc.vendor.tax_id}",
    ]
    ship_rows = [doc.buyer.name, *doc.buyer.address_lines]
    b1 = _party_box(p, m, p.y, box_w, "VENDOR", vendor_rows)
    b2 = _party_box(p, m + box_w + 16, p.y, box_w, "SHIP TO", ship_rows)
    p.y = min(b1, b2) - 22

    cols = [
        Column("#", 22),
        Column("SKU", 92),
        Column("Description", 201),
        Column("Qty", 40, "right"),
        Column("Unit Price", 70, "right"),
        Column(f"Amount ({r.currency})", 70, "right"),
    ]
    scale = (w - 2 * m) / sum(c.width for c in cols)
    cols = [Column(c.title, c.width * scale, c.align) for c in cols]
    rows = [
        [
            str(i + 1),
            li.sku or "",
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        for i, li in enumerate(r.line_items)
    ]
    p.table(cols, rows, header_fill=st.accent, header_text=white)

    _totals(
        p,
        [
            ("Subtotal", money(r.subtotal, r.currency, st)),
            (tax_label(doc.vendor, doc.tax_rate), money(r.tax_amount, r.currency, st)),
            (f"Total {r.currency}", money(r.total, r.currency, st)),
        ],
    )

    p.y -= 18
    p.lines(
        m,
        p.y,
        [
            f"Please quote PO number {r.po_number} on all invoices and delivery notes.",
            "Goods are subject to inspection on receipt. Partial deliveries accepted.",
        ],
        size=8,
        color=GREY,
    )
    p.y -= 40
    p.text(m, p.y, f"Authorised by: {doc.contact}, Procurement", size=9)
    p.finish()
    return buf.getvalue(), p.pages


def render_invoice(doc: InvoiceDoc) -> tuple[bytes, int]:
    r, st = doc.record, doc.style
    buf = io.BytesIO()
    w, h = page_size_for(doc.seller)
    p = Page(
        new_canvas(buf, (w, h), f"Invoice {r.invoice_number}"), st, w, h, footer_note=FICTION_NOTE
    )
    m = p.margin

    p.text(m, p.y - 14, doc.seller.name, size=15, bold=True, color=st.accent)
    y = p.lines(m, p.y - 30, list(doc.seller.address_lines), size=8.5, color=GREY)
    p.text(m, y, f"{tax_id_label(doc.seller)}: {doc.seller.tax_id}", size=8.5, color=GREY)
    p.text(w - m, p.y - 18, "INVOICE", size=22, bold=True, align="right")
    facts = [
        ("Invoice No.", r.invoice_number or ""),
        ("Invoice Date", fmt_date(r.invoice_date, st.date_style) if r.invoice_date else ""),
        ("Due Date", fmt_date(r.due_date, st.date_style) if r.due_date else ""),
    ]
    if r.po_number:
        facts.append(("Your PO Ref.", r.po_number))
    fy = p.y - 40
    for label, value in facts:
        p.text(w - m - 190, fy, label, size=8.5, color=GREY)
        p.text(w - m, fy, value, size=9, bold=True, align="right")
        fy -= 13
    p.y = min(fy, p.y - 80) - 12

    box_w = (w - 2 * m - 16) / 2
    b = _party_box(p, m, p.y, box_w, "BILL TO", [r.buyer.name or "", *doc.buyer.address_lines])
    p.y = b - 22

    show_sku = any(li.sku for li in r.line_items)
    cols = [
        Column("Description", 255),
        Column("Qty", 45, "right"),
        Column("Unit Price", 80, "right"),
        Column(f"Amount ({r.currency})", 80, "right"),
    ]
    if show_sku:
        cols.insert(0, Column("Item Code", 90))
    scale = (w - 2 * m) / sum(c.width for c in cols)
    cols = [Column(c.title, c.width * scale, c.align) for c in cols]
    rows: list[list[str]] = []
    for li in r.line_items:
        row = [
            li.description or "",
            fmt_qty(li.quantity or Decimal(0)),
            fmt_amount(li.unit_price or Decimal(0), st.money_style),
            fmt_amount(li.line_total or Decimal(0), st.money_style),
        ]
        if show_sku:
            row.insert(0, li.sku or "")
        rows.append(row)
    p.table(cols, rows, header_fill=st.accent, header_text=white)

    _totals(
        p,
        [
            ("Subtotal", money(r.subtotal, r.currency, st)),
            (tax_label(doc.seller, doc.tax_rate), money(r.tax_amount, r.currency, st)),
            (f"Amount Due {r.currency}", money(r.total, r.currency, st)),
        ],
    )

    p.y -= 18
    p.lines(
        m,
        p.y,
        [
            f"Payment terms: {doc.payment_terms}. Please pay by bank transfer quoting "
            f"invoice number {r.invoice_number}.",
            "Thank you for your business.",
        ],
        size=8,
        color=GREY,
    )
    p.finish()
    return buf.getvalue(), p.pages

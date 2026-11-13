"""Shared drawing helpers for the PDF templates (reportlab, base-14 fonts only).

Canvases are created with `invariant=1` so output is byte-identical for the same input.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.utils import simpleSplit  # pyright: ignore[reportUnknownVariableType]
from reportlab.pdfgen.canvas import Canvas

from evals.generator.catalog import Company
from evals.generator.money import DateStyle, MoneyStyle

Align = Literal["left", "right", "center"]

BLACK = HexColor("#1a1a1a")
GREY = HexColor("#6b6b6b")
RULE = HexColor("#c8c8c8")


@dataclass(frozen=True)
class Style:
    date_style: DateStyle
    money_style: MoneyStyle
    accent: Color
    font: str = "Helvetica"
    bold: str = "Helvetica-Bold"


@dataclass(frozen=True)
class Column:
    title: str
    width: float
    align: Align = "left"


def page_size_for(company: Company) -> tuple[float, float]:
    return LETTER if company.country == "US" else A4


def new_canvas(buf: io.BytesIO, pagesize: tuple[float, float], title: str) -> Canvas:
    c = Canvas(buf, pagesize=pagesize, invariant=1, pageCompression=1)
    c.setTitle(title)
    c.setAuthor("DocAgent synthetic data generator (fictional)")
    return c


@dataclass
class Page:
    """Cursor-based writer over a canvas with automatic page breaks for tables."""

    c: Canvas
    style: Style
    width: float
    height: float
    margin: float = 50.0
    y: float = 0.0
    footer_note: str = ""
    _pages: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        self.y = self.height - self.margin

    @property
    def pages(self) -> int:
        return self._pages

    # -- text ---------------------------------------------------------------
    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: float = 9,
        bold: bool = False,
        color: Color = BLACK,
        align: Align = "left",
        font: str | None = None,
    ) -> None:
        self.c.setFillColor(color)
        self.c.setFont(font or (self.style.bold if bold else self.style.font), size)
        if align == "right":
            self.c.drawRightString(x, y, s)
        elif align == "center":
            self.c.drawCentredString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def lines(
        self,
        x: float,
        y: float,
        rows: Sequence[str],
        *,
        size: float = 9,
        leading: float | None = None,
        bold_first: bool = False,
        color: Color = BLACK,
    ) -> float:
        """Draw stacked lines; return the y below the last one."""
        lead = leading or size * 1.3
        for i, row in enumerate(rows):
            self.text(x, y, row, size=size, bold=bold_first and i == 0, color=color)
            y -= lead
        return y

    def label_value(
        self, x: float, y: float, label: str, value: str, *, label_w: float = 80, size: float = 9
    ) -> None:
        self.text(x, y, label, size=size, color=GREY)
        self.text(x + label_w, y, value, size=size, bold=True)

    def rule(
        self,
        y: float,
        *,
        x0: float | None = None,
        x1: float | None = None,
        color: Color = RULE,
        width: float = 0.6,
    ) -> None:
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(
            x0 if x0 is not None else self.margin,
            y,
            x1 if x1 is not None else self.width - self.margin,
            y,
        )

    # -- pages --------------------------------------------------------------
    def footer(self) -> None:
        self.text(self.margin, 28, self.footer_note, size=7, color=GREY)
        self.text(
            self.width - self.margin, 28, f"Page {self._pages}", size=7, color=GREY, align="right"
        )

    def new_page(self) -> None:
        self.footer()
        self.c.showPage()
        self._pages += 1
        self.y = self.height - self.margin

    def ensure(self, space: float) -> None:
        """Start a new page unless `space` points fit above the bottom margin."""
        if self.y - space < self.margin + 20:
            self.new_page()

    def fit_columns(self, columns: Sequence[Column]) -> list[Column]:
        """Scale column widths proportionally to the printable width."""
        scale = (self.width - 2 * self.margin) / sum(c.width for c in columns)
        return [Column(c.title, c.width * scale, c.align) for c in columns]

    def finish(self) -> None:
        self.footer()
        self.c.showPage()
        self.c.save()

    # -- tables -------------------------------------------------------------
    def table(
        self,
        columns: Sequence[Column],
        rows: Sequence[Sequence[str]],
        *,
        size: float = 8.5,
        header_fill: Color | None = None,
        header_text: Color = BLACK,
        zebra: Color | None = None,
        grid: bool = False,
        reserve_below: float = 120,
    ) -> None:
        """Draw a table at self.y, wrapping cell text and breaking pages as needed.

        `reserve_below` keeps room under the last row for the totals block.
        """
        lead = size * 1.25
        pad = 4.0
        x0 = self.margin
        total_w = sum(col.width for col in columns)

        def header() -> None:
            h = lead + 2 * pad
            if header_fill is not None:
                self.c.setFillColor(header_fill)
                self.c.rect(x0, self.y - h, total_w, h, stroke=0, fill=1)
            x = x0
            for col in columns:
                tx = x + col.width - pad if col.align == "right" else x + pad
                self.text(
                    tx,
                    self.y - pad - size,
                    col.title,
                    size=size,
                    bold=True,
                    color=header_text,
                    align="right" if col.align == "right" else "left",
                )
                x += col.width
            self.y -= h
            if header_fill is None:
                self.rule(self.y, x0=x0, x1=x0 + total_w, color=BLACK, width=0.8)

        header()
        for i, row in enumerate(rows):
            wrapped: list[list[str]] = [
                cast(list[str], simpleSplit(cell, self.style.font, size, col.width - 2 * pad))
                for cell, col in zip(row, columns, strict=True)
            ]
            h = max(len(w) for w in wrapped) * lead + 2 * pad
            is_last = i == len(rows) - 1
            if self.y - h < self.margin + (reserve_below if is_last else 30):
                self.new_page()
                header()
            if zebra is not None and i % 2 == 1:
                self.c.setFillColor(zebra)
                self.c.rect(x0, self.y - h, total_w, h, stroke=0, fill=1)
            x = x0
            for cell_lines, col in zip(wrapped, columns, strict=True):
                ty = self.y - pad - size
                for ln in cell_lines:
                    tx = x + col.width - pad if col.align == "right" else x + pad
                    self.text(
                        tx, ty, ln, size=size, align="right" if col.align == "right" else "left"
                    )
                    ty -= lead
                x += col.width
            if grid:
                self.c.setStrokeColor(RULE)
                self.c.setLineWidth(0.5)
                self.c.rect(x0, self.y - h, total_w, h, stroke=1, fill=0)
                x = x0
                for col in columns[:-1]:
                    x += col.width
                    self.c.line(x, self.y, x, self.y - h)
            self.y -= h
            if not grid:
                self.rule(self.y, x0=x0, x1=x0 + total_w)
